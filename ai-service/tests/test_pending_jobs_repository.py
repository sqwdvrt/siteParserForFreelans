"""Unit tests for PostgresPendingJobsRepository without real database."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from ai_service.adapter.postgres import PostgresPendingJobsRepository


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.execute_calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self._rows = rows or []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.execute_calls.append((sql, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self, *_args: object, **_kwargs: object) -> _FakeCursor:
        return self._cursor


@contextmanager
def _fake_conn_ctx(conn: _FakeConn):
    yield conn


def test_upsert_executes_insert_on_conflict(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.upsert(user_id=10, job_id=20, match_score=0.85)

    assert len(cursor.execute_calls) == 1
    sql, params = cursor.execute_calls[0]
    assert "INSERT INTO pending_ac_jobs" in sql
    assert "ON CONFLICT (user_id, job_id)" in sql
    assert "trace_id" in sql
    assert params == (10, 20, 0.85, "")


def test_upsert_with_trace_id_normalizes_and_persists(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.upsert(user_id=10, job_id=20, match_score=0.85, trace_id=" trace-x ")

    assert len(cursor.execute_calls) == 1
    _sql, params = cursor.execute_calls[0]
    assert params == (10, 20, 0.85, "trace-x")


def test_mark_processed_skips_when_job_ids_empty(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.mark_processed(user_id=10, job_ids=[])

    assert cursor.execute_calls == []


def test_mark_processed_updates_only_selected_jobs(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.mark_processed(user_id=10, job_ids=[1, 2, 3])

    assert len(cursor.execute_calls) == 1
    sql, params = cursor.execute_calls[0]
    assert "UPDATE pending_ac_jobs" in sql
    assert "job_id = ANY(%s)" in sql
    assert params == (10, [1, 2, 3])


def test_list_unprocessed_user_ids_returns_ints(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor(rows=[{"user_id": 1}, {"user_id": 2}])
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    result = repo.list_unprocessed_user_ids()

    assert result == [1, 2]
    assert len(cursor.execute_calls) == 1
    assert "SELECT DISTINCT user_id" in cursor.execute_calls[0][0]


def test_list_unprocessed_job_ids_returns_ints(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor(rows=[{"job_id": 5}, {"job_id": 8}])
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    result = repo.list_unprocessed_job_ids(user_id=10, limit=20)

    assert result == [5, 8]
    assert len(cursor.execute_calls) == 1
    sql, params = cursor.execute_calls[0]
    assert "SELECT job_id" in sql
    assert "LIMIT %s" in sql
    assert params == (10, 20)


def test_list_unprocessed_job_ids_with_trace_returns_first_non_empty_trace(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor(
        rows=[
            {"job_id": 5, "trace_id": ""},
            {"job_id": 8, "trace_id": "trace-8"},
            {"job_id": 9, "trace_id": "trace-9"},
        ]
    )
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    job_ids, trace_id = repo.list_unprocessed_job_ids_with_trace(user_id=10, limit=20)

    assert job_ids == [5, 8, 9]
    assert trace_id == "trace-8"
    assert len(cursor.execute_calls) == 1
    sql, params = cursor.execute_calls[0]
    assert "SELECT job_id, COALESCE(trace_id, '') AS trace_id" in sql
    assert params == (10, 20)
