"""Shared PostgreSQL pooling primitives for repositories."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Generator

from pgvector.psycopg2 import register_vector
from psycopg2.extensions import connection as PGConnection
from psycopg2.pool import AbstractConnectionPool, ThreadedConnectionPool


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
        self._dsn = dsn
        self._minconn = minconn
        self._maxconn = maxconn
        self._statement_timeout_ms = statement_timeout_ms
        self._pool: AbstractConnectionPool | None = None
        self._pool_lock = threading.Lock()

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

    def close(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()
