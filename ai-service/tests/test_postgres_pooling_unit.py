"""Unit tests for pooled PostgreSQL repository behavior."""

from __future__ import annotations

import pytest

from ai_service.adapter.postgres import PostgresMatchRepository
from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository


class _FakeCursor:
    def __init__(self) -> None:
        self.execute_calls: list[tuple] = []

    def execute(self, sql: str, params=None) -> None:
        self.execute_calls.append((sql, params))

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _FakeConn:
    def __init__(self) -> None:
        self.closed = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.cursor_obj = _FakeCursor()

    def cursor(self):
        return self.cursor_obj

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


def _make_fake_pool_fixture(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    created: list[_FakePool] = []

    def fake_pool(minconn: int, maxconn: int, dsn: str) -> _FakePool:
        pool = _FakePool(minconn, maxconn, dsn)
        created.append(pool)
        return pool

    monkeypatch.setattr(
        "ai_service.adapter.postgres._pooled_repository._VectorThreadedConnectionPool",
        fake_pool,
    )
    return created


def test_statement_timeout_set_local_on_each_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _make_fake_pool_fixture(monkeypatch)
    repo = _DummyRepo("postgresql://fake/fake", statement_timeout_ms=5_000)

    with repo._conn():
        pass
    with repo._conn():
        pass

    pool = created[0]
    assert pool.conn.cursor_obj.execute_calls == [
        ("SET LOCAL statement_timeout = %s", ("5000ms",)),
        ("SET LOCAL statement_timeout = %s", ("5000ms",)),
    ]


def test_statement_timeout_zero_skips_set_local(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _make_fake_pool_fixture(monkeypatch)
    repo = _DummyRepo("postgresql://fake/fake", statement_timeout_ms=0)

    with repo._conn():
        pass

    pool = created[0]
    assert pool.conn.cursor_obj.execute_calls == []


def test_statement_timeout_negative_raises() -> None:
    with pytest.raises(ValueError, match="statement_timeout_ms"):
        _DummyRepo("postgresql://fake/fake", statement_timeout_ms=-1)


def test_statement_timeout_default_is_10s(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _make_fake_pool_fixture(monkeypatch)
    repo = _DummyRepo("postgresql://fake/fake")

    with repo._conn():
        pass

    pool = created[0]
    assert pool.conn.cursor_obj.execute_calls == [
        ("SET LOCAL statement_timeout = %s", ("10000ms",)),
    ]
