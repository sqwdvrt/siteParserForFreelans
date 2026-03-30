"""Shared PostgreSQL pooling primitives for repositories."""

from __future__ import annotations

import contextlib
import hashlib
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pgvector.psycopg2 import register_vector
from psycopg2.extensions import connection as PGConnection
from psycopg2.pool import AbstractConnectionPool, ThreadedConnectionPool

from ai_service.util.fallback_metrics import PoolStats, deregister_pool_provider, register_pool_provider


class _VectorThreadedConnectionPool(ThreadedConnectionPool):
    """Thread-safe pool that auto-registers pgvector on new connections."""

    def _connect(self, key=None) -> PGConnection:  # type: ignore[override]
        conn = super()._connect(key)
        register_vector(conn)
        return conn


@dataclass
class _SharedPoolEntry:
    metrics_name: str
    pool: AbstractConnectionPool | None = None
    refcount: int = 0
    active_leases: int = 0
    closing: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_SHARED_POOLS: dict[tuple[str, int, int], _SharedPoolEntry] = {}
_SHARED_POOLS_LOCK = threading.RLock()


def _metrics_name_for_pool_key(pool_key: tuple[str, int, int]) -> str:
    digest = hashlib.sha256(repr(pool_key).encode("utf-8")).hexdigest()[:12]
    return f"postgres_pool_{digest}"


def _snapshot_shared_pool_stats(pool_key: tuple[str, int, int]) -> PoolStats:
    with _SHARED_POOLS_LOCK:
        entry = _SHARED_POOLS.get(pool_key)
        pool = entry.pool if entry is not None else None
    if pool is None:
        return PoolStats(active=0, idle=0, max=pool_key[2])
    idle = len(getattr(pool, "_pool", []))
    active = len(getattr(pool, "_used", {}))
    return PoolStats(active=active, idle=idle, max=pool_key[2])


class PooledPostgresRepository:
    """Base class with lazy-initialized PostgreSQL connection pool."""

    def __init__(
        self,
        dsn: str,
        *,
        minconn: int = 1,
        maxconn: int = 10,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        if minconn < 0:
            raise ValueError("minconn must be >= 0")
        if maxconn < minconn:
            raise ValueError("maxconn must be >= minconn")
        if statement_timeout_ms < 0:
            raise ValueError("statement_timeout_ms must be >= 0")
        self._dsn = _sanitize_dsn_for_psycopg2(dsn)
        self._minconn = minconn
        self._maxconn = maxconn
        self._statement_timeout_ms = statement_timeout_ms
        self._pool_key = (self._dsn, self._minconn, self._maxconn)
        self._closed = False
        self._pool: AbstractConnectionPool | None = None
        self._pool_lock = threading.Lock()
        self._retain_shared_pool_entry()

    def _get_pool(self) -> AbstractConnectionPool:
        with self._pool_lock:
            if self._closed:
                raise RuntimeError("repository is closed")
            if self._pool is None:
                self._pool = self._ensure_shared_pool()
            return self._pool

    def _retain_shared_pool_entry(self) -> None:
        with _SHARED_POOLS_LOCK:
            entry = _SHARED_POOLS.get(self._pool_key)
            if entry is None:
                entry = _SharedPoolEntry(
                    metrics_name=_metrics_name_for_pool_key(self._pool_key),
                )
                _SHARED_POOLS[self._pool_key] = entry
                register_pool_provider(
                    entry.metrics_name,
                    lambda pool_key=self._pool_key: _snapshot_shared_pool_stats(pool_key),
                )
            entry.refcount += 1
            entry.closing = False

    def _ensure_shared_pool(self) -> AbstractConnectionPool:
        with _SHARED_POOLS_LOCK:
            entry = _SHARED_POOLS.get(self._pool_key)
            if entry is None:
                raise RuntimeError("shared pool entry is missing")
        with entry.lock:
            if entry.pool is None:
                entry.pool = _VectorThreadedConnectionPool(
                    self._minconn,
                    self._maxconn,
                    self._dsn,
                )
            return entry.pool

    def _borrow_pool(self) -> AbstractConnectionPool:
        with self._pool_lock:
            if self._closed:
                raise RuntimeError("repository is closed")
            if self._pool is None:
                self._pool = self._ensure_shared_pool()
            pool = self._pool
            with _SHARED_POOLS_LOCK:
                entry = _SHARED_POOLS.get(self._pool_key)
                if entry is None:
                    raise RuntimeError("shared pool entry is missing")
                entry.active_leases += 1
            return pool

    def _release_pool_lease(self) -> None:
        pool_to_close: AbstractConnectionPool | None = None
        metrics_name: str | None = None
        with _SHARED_POOLS_LOCK:
            entry = _SHARED_POOLS.get(self._pool_key)
            if entry is None:
                return
            if entry.active_leases > 0:
                entry.active_leases -= 1
            if entry.closing and entry.refcount == 0 and entry.active_leases == 0:
                pool_to_close = entry.pool
                metrics_name = entry.metrics_name
                del _SHARED_POOLS[self._pool_key]
        if metrics_name is not None:
            deregister_pool_provider(metrics_name)
        if pool_to_close is not None:
            pool_to_close.closeall()

    def _release_shared_pool_ref(self) -> None:
        pool_to_close: AbstractConnectionPool | None = None
        metrics_name: str | None = None
        with _SHARED_POOLS_LOCK:
            entry = _SHARED_POOLS.get(self._pool_key)
            if entry is None:
                return
            if entry.refcount > 0:
                entry.refcount -= 1
            if entry.refcount == 0:
                entry.closing = True
                if entry.active_leases == 0:
                    pool_to_close = entry.pool
                    metrics_name = entry.metrics_name
                    del _SHARED_POOLS[self._pool_key]
        if metrics_name is not None:
            deregister_pool_provider(metrics_name)
        if pool_to_close is not None:
            pool_to_close.closeall()

    @contextlib.contextmanager
    def _conn(self) -> Generator[PGConnection, None, None]:
        pool = self._borrow_pool()
        try:
            conn = pool.getconn()
        except Exception:
            self._release_pool_lease()
            raise
        rollback_failed = False
        try:
            if self._statement_timeout_ms > 0:
                with conn.cursor() as cur:
                    cur.execute(
                        "SET LOCAL statement_timeout = %s",
                        (f"{self._statement_timeout_ms}ms",),
                    )
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                rollback_failed = True
            raise
        finally:
            close_conn = rollback_failed or bool(conn.closed)
            try:
                pool.putconn(conn, close=close_conn)
            except Exception:
                with contextlib.suppress(Exception):
                    conn.close()
            self._release_pool_lease()

    def _pool_stats(self) -> PoolStats:
        """Return active/idle/max connection counts for Prometheus metrics.

        Reads psycopg2 pool internals:
          _pool  – list of idle connections available for checkout
          _used  – dict mapping connection → key for in-use connections
        Values are approximate (no extra locking) which is acceptable for gauges.
        """
        return _snapshot_shared_pool_stats(self._pool_key)

    def close(self) -> None:
        with self._pool_lock:
            if self._closed:
                return
            self._closed = True
            self._pool = None
        self._release_shared_pool_ref()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


def _sanitize_dsn_for_psycopg2(dsn: str) -> str:
    """Remove pgx-only URL params that psycopg2 cannot parse."""
    try:
        parts = urlsplit(dsn)
    except Exception:
        return dsn
    if not parts.query:
        return dsn
    query = parse_qsl(parts.query, keep_blank_values=True)
    filtered = [(k, v) for (k, v) in query if k != "default_query_exec_mode"]
    if len(filtered) == len(query):
        return dsn
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(filtered), parts.fragment))
