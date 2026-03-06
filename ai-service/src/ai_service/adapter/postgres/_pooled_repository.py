"""Shared PostgreSQL pooling primitives for repositories."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Generator
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
        if minconn < 1:
            raise ValueError("minconn must be >= 1")
        if maxconn < minconn:
            raise ValueError("maxconn must be >= minconn")
        if statement_timeout_ms < 0:
            raise ValueError("statement_timeout_ms must be >= 0")
        self._dsn = _sanitize_dsn_for_psycopg2(dsn)
        self._minconn = minconn
        self._maxconn = maxconn
        self._statement_timeout_ms = statement_timeout_ms
        self._pool: AbstractConnectionPool | None = None
        self._pool_lock = threading.Lock()
        # Register metrics provider keyed by concrete class name.
        # _pool_stats() reads psycopg2 internal attributes (_pool, _used) which
        # are stable across psycopg2 versions and already used by our _connect override.
        self._pool_metrics_name = type(self).__name__
        register_pool_provider(self._pool_metrics_name, self._pool_stats)

    def _get_pool(self) -> AbstractConnectionPool:
        pool = self._pool
        if pool is not None:
            return pool
        with self._pool_lock:
            if self._pool is None:
                self._pool = _VectorThreadedConnectionPool(
                    self._minconn,
                    self._maxconn,
                    self._dsn,
                )
            return self._pool

    @contextlib.contextmanager
    def _conn(self) -> Generator[PGConnection, None, None]:
        pool = self._get_pool()
        conn = pool.getconn()
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

    def _pool_stats(self) -> PoolStats:
        """Return active/idle/max connection counts for Prometheus metrics.

        Reads psycopg2 pool internals:
          _pool  – list of idle connections available for checkout
          _used  – dict mapping connection → key for in-use connections
        Values are approximate (no extra locking) which is acceptable for gauges.
        """
        pool = self._pool
        if pool is None:
            return PoolStats(active=0, idle=0, max=self._maxconn)
        idle = len(getattr(pool, "_pool", []))
        active = len(getattr(pool, "_used", {}))
        return PoolStats(active=active, idle=idle, max=self._maxconn)

    def close(self) -> None:
        deregister_pool_provider(self._pool_metrics_name)
        with self._pool_lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None

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
