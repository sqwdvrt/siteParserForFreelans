"""Unit tests for PostgresPendingJobsRepository without real database."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from ai_service.adapter.postgres import PostgresPendingJobsRepository


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.execute_calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.executemany_calls: list[tuple[str, list[tuple[Any, ...]]]] = []
        self._rows = rows or []
        self._one_row: tuple[Any, ...] | None = None
        self.rowcount = 0

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.execute_calls.append((sql, params))

    def executemany(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        self.executemany_calls.append((sql, params_seq))

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one_row

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
    assert "rerank_score" in sql
    assert "raw_similarity" in sql
    assert "final_score" in sql
    assert "feedback_bonus" in sql
    assert "preference_multiplier" in sql
    assert "reason_codes" in sql
    assert "trace_id" in sql
    assert params == (10, 20, 0.85, 0.0, 0.0, 0.0, 1.0, 0.0, "", [], "")


def test_upsert_with_trace_id_normalizes_and_persists(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.upsert(
        user_id=10,
        job_id=20,
        match_score=0.85,
        rerank_score=0.72,
        raw_similarity=0.8,
        feedback_bonus=0.12,
        preference_multiplier=1.2,
        final_score=0.83,
        ranker_version="v2",
        reason_codes=["fresh_job"],
        trace_id=" trace-x ",
    )

    assert len(cursor.execute_calls) == 1
    _sql, params = cursor.execute_calls[0]
    assert params == (10, 20, 0.85, 0.72, 0.8, 0.12, 1.2, 0.83, "v2", ["fresh_job"], "trace-x")


def test_upsert_many_executes_single_bulk_query(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))
    calls: list[
        tuple[
            str,
            list[tuple[int, int, float, float, float, float, float, float, str, list[str] | None, str]],
            str | None,
        ]
    ] = []

    def fake_execute_values(cur, sql, argslist, template=None, **_kwargs) -> None:
        assert cur is cursor
        calls.append((sql, list(argslist), template))

    monkeypatch.setattr(
        "ai_service.adapter.postgres.pending_jobs_repository.execute_values",
        fake_execute_values,
    )

    repo.upsert_many(
        [
            (10, 20, 0.85, 0.74, 0.81, 0.83, "v2", ["fresh_job"], " trace-1 "),
            (30, 40, 0.65, 0.58, 0.61, 0.6, "v2", ["standard_recency"], ""),
        ]
    )

    assert cursor.execute_calls == []
    assert len(calls) == 1
    sql, argslist, template = calls[0]
    assert "INSERT INTO pending_ac_jobs" in sql
    assert "ON CONFLICT (user_id, job_id)" in sql
    assert template == "(%s, %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, ''), %s, NULLIF(%s, ''))"
    assert argslist == [
        (10, 20, 0.85, 0.74, 0.81, 0.0, 1.0, 0.83, "v2", ["fresh_job"], "trace-1"),
        (30, 40, 0.65, 0.58, 0.61, 0.0, 1.0, 0.6, "v2", ["standard_recency"], ""),
    ]


def test_upsert_many_merges_duplicate_rows(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))
    captured_argslist: list[tuple[int, int, float, float, float, float, float, float, str, list[str], str]] = []

    def fake_execute_values(cur, _sql, argslist, template=None, **_kwargs) -> None:
        assert cur is cursor
        _ = template
        captured_argslist.extend(list(argslist))

    monkeypatch.setattr(
        "ai_service.adapter.postgres.pending_jobs_repository.execute_values",
        fake_execute_values,
    )

    repo.upsert_many(
        [
            (10, 20, 0.4, 0.3, 0.4, 0.4, "v2", ["baseline_similarity"], ""),
            (10, 20, 0.9, 0.8, 0.9, 0.9, "v2", ["high_similarity"], "trace-first"),
            (10, 20, 0.6, 0.7, 0.6, 0.6, "v2", ["standard_recency"], "trace-last"),
            (11, 21, 0.3, 0.2, 0.3, 0.3, "v2", ["baseline_similarity"], ""),
        ]
    )

    assert captured_argslist == [
        (10, 20, 0.9, 0.8, 0.9, 0.0, 1.0, 0.9, "v2", ["high_similarity"], "trace-last"),
        (11, 21, 0.3, 0.2, 0.3, 0.0, 1.0, 0.3, "v2", ["baseline_similarity"], ""),
    ]


def test_upsert_many_accepts_legacy_row_shape(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))
    captured_argslist: list[tuple[int, int, float, float, float, float, float, float, str, list[str], str]] = []

    def fake_execute_values(cur, _sql, argslist, template=None, **_kwargs) -> None:
        assert cur is cursor
        _ = template
        captured_argslist.extend(list(argslist))

    monkeypatch.setattr(
        "ai_service.adapter.postgres.pending_jobs_repository.execute_values",
        fake_execute_values,
    )

    repo.upsert_many(
        [
            (10, 20, 0.85, 0.81, 0.83, "v2", ["fresh_job"], "trace-1"),
        ]
    )

    assert captured_argslist == [
        (10, 20, 0.85, 0.0, 0.81, 0.0, 1.0, 0.83, "v2", ["fresh_job"], "trace-1"),
    ]


def test_save_scoring_components_updates_rows(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.save_scoring_components(
        user_id=10,
        rows=[
            (1, 0.91, 0.1, 1.2, 1.2012),
            (2, 0.74, -0.2, 0.8, 0.4736),
        ],
    )

    assert len(cursor.executemany_calls) == 1
    sql, params_seq = cursor.executemany_calls[0]
    assert "UPDATE pending_ac_jobs" in sql
    assert "feedback_bonus = %s" in sql
    assert "preference_multiplier = %s" in sql
    assert params_seq == [
        (0.91, 0.1, 1.2, 1.2012, 10, 1),
        (0.74, -0.2, 0.8, 0.4736, 10, 2),
    ]


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


def test_release_claim_skips_when_job_ids_empty(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.release_claim(user_id=10, job_ids=[])

    assert cursor.execute_calls == []


def test_release_claim_clears_queue_lease_for_selected_jobs(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    repo.release_claim(user_id=10, job_ids=[1, 2, 3])

    assert len(cursor.execute_calls) == 1
    sql, params = cursor.execute_calls[0]
    assert "UPDATE pending_ac_jobs" in sql
    assert "SET queued_at = NULL" in sql
    assert "job_id = ANY(%s)" in sql
    assert params == (10, [1, 2, 3])


def test_count_rows_queries_total_pending_rows(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    cursor._one_row = (42,)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    result = repo.count_rows()

    assert result == 42
    sql, params = cursor.execute_calls[0]
    assert "SELECT COUNT(*) FROM pending_ac_jobs" in sql
    assert params is None


def test_count_unprocessed_rows_queries_only_unprocessed(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    cursor._one_row = (7,)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    result = repo.count_unprocessed_rows()

    assert result == 7
    sql, params = cursor.execute_calls[0]
    assert "WHERE processed_at IS NULL" in sql
    assert params is None


def test_delete_processed_older_than_deletes_only_old_processed_rows(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    cursor.rowcount = 5
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    result = repo.delete_processed_older_than(14)

    assert result == 5
    sql, params = cursor.execute_calls[0]
    assert "DELETE FROM pending_ac_jobs" in sql
    assert "processed_at IS NOT NULL" in sql
    assert "make_interval(days => %s)" in sql
    assert params == (14,)


def test_delete_processed_older_than_skips_non_positive_retention(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    result = repo.delete_processed_older_than(0)

    assert result == 0
    assert cursor.execute_calls == []


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


def test_claim_unprocessed_job_ids_returns_ints(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor(rows=[{"job_id": 5, "trace_id": ""}, {"job_id": 8, "trace_id": "trace-8"}])
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    result = repo.claim_unprocessed_job_ids(user_id=10, limit=20, lease_timeout_sec=900)

    assert result == [5, 8]
    assert len(cursor.execute_calls) == 1
    sql, params = cursor.execute_calls[0]
    assert "WITH to_claim AS (" in sql
    assert "eligible AS (" in sql
    assert "eligible.cnt >= %s" in sql
    assert "RETURNING p.job_id, to_claim.trace_id" in sql
    assert params == (10, 900, 20, 1)


def test_claim_unprocessed_job_ids_with_trace_returns_first_non_empty_trace(monkeypatch) -> None:
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

    job_ids, trace_id = repo.claim_unprocessed_job_ids_with_trace(
        user_id=10,
        limit=20,
        lease_timeout_sec=600,
    )

    assert job_ids == [5, 8, 9]
    assert trace_id == "trace-8"
    assert len(cursor.execute_calls) == 1
    sql, params = cursor.execute_calls[0]
    assert "WITH to_claim AS (" in sql
    assert "eligible AS (" in sql
    assert "eligible.cnt >= %s" in sql
    assert "RETURNING p.job_id, to_claim.trace_id" in sql
    assert params == (10, 600, 20, 1)


def test_claim_unprocessed_job_ids_with_trace_respects_min_jobs_param(monkeypatch) -> None:
    repo = PostgresPendingJobsRepository("postgresql://fake/fake")
    cursor = _FakeCursor(rows=[])
    conn = _FakeConn(cursor)
    monkeypatch.setattr(repo, "_conn", lambda: _fake_conn_ctx(conn))

    job_ids, trace_id = repo.claim_unprocessed_job_ids_with_trace(
        user_id=10,
        limit=20,
        lease_timeout_sec=600,
        min_jobs=3,
    )

    assert job_ids == []
    assert trace_id == ""
    assert len(cursor.execute_calls) == 1
    _sql, params = cursor.execute_calls[0]
    assert params == (10, 600, 20, 3)
