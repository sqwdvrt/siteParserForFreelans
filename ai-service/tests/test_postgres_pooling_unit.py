"""Unit tests for pooled PostgreSQL repository behavior."""

from __future__ import annotations

import pytest

from ai_service.adapter.postgres import PostgresMatchRepository
from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository


class _FakeConn:
    def __init__(self) -> None:
        self.closed = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.close_calls += 1
        self.closed = 1


class _FakePool:
    def __init__(self, minconn: int, maxconn: int, dsn: str) -> None:
        self.minconn = minconn
        self.maxconn = maxconn
        self.dsn = dsn
        self.conn = _FakeConn()
        self.getconn_calls = 0
        self.putconn_calls: list[bool] = []
        self.closeall_calls = 0

    def getconn(self):
        self.getconn_calls += 1
        return self.conn

    def putconn(self, conn, close: bool = False) -> None:
        assert conn is self.conn
        self.putconn_calls.append(close)

    def closeall(self) -> None:
        self.closeall_calls += 1


class _DummyRepo(PooledPostgresRepository):
    pass


def test_pool_created_lazily_for_invalid_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    created = {"count": 0}

    def fake_pool(minconn: int, maxconn: int, dsn: str) -> _FakePool:
        created["count"] += 1
        return _FakePool(minconn, maxconn, dsn)

    monkeypatch.setattr(
        "ai_service.adapter.postgres._pooled_repository._VectorThreadedConnectionPool",
        fake_pool,
    )
    repo = PostgresMatchRepository("postgresql://fake/fake")

    assert repo.find_users_for_job([], job_id=1, threshold=0.5) == []
    assert created["count"] == 0


def test_pool_reused_and_connections_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_FakePool] = []

    def fake_pool(minconn: int, maxconn: int, dsn: str) -> _FakePool:
        pool = _FakePool(minconn, maxconn, dsn)
        created.append(pool)
        return pool

    monkeypatch.setattr(
        "ai_service.adapter.postgres._pooled_repository._VectorThreadedConnectionPool",
        fake_pool,
    )
    repo = _DummyRepo("postgresql://fake/fake", minconn=2, maxconn=5)

    with repo._conn():
        pass
    with repo._conn():
        pass

    assert len(created) == 1
    pool = created[0]
    assert pool.minconn == 2
    assert pool.maxconn == 5
    assert pool.getconn_calls == 2
    assert pool.putconn_calls == [False, False]
    assert pool.conn.commit_calls == 2

    repo.close()
    assert pool.closeall_calls == 1


def test_rollback_on_error_and_return_to_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakePool] = []

    def fake_pool(minconn: int, maxconn: int, dsn: str) -> _FakePool:
        pool = _FakePool(minconn, maxconn, dsn)
        created.append(pool)
        return pool

    monkeypatch.setattr(
        "ai_service.adapter.postgres._pooled_repository._VectorThreadedConnectionPool",
        fake_pool,
    )
    repo = _DummyRepo("postgresql://fake/fake")

    with pytest.raises(RuntimeError):
        with repo._conn():
            raise RuntimeError("boom")

    pool = created[0]
    assert pool.conn.rollback_calls == 1
    assert pool.putconn_calls == [False]
