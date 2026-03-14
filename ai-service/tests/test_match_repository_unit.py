"""Unit-тесты PostgresMatchRepository без БД."""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

from ai_service.adapter.postgres import PostgresMatchRepository


def test_invalid_embedding_returns_empty() -> None:
    """При None, пустом или len≠384 — возвращает [] без обращения к БД."""
    repo = PostgresMatchRepository("postgresql://fake/fake")
    job_id = 1
    assert repo.find_users_for_job(None, job_id, 0.5) == []  # type: ignore[arg-type]
    assert repo.find_users_for_job([], job_id, 0.5) == []
    assert repo.find_users_for_job([0.1] * 10, job_id, 0.5) == []


def test_find_users_for_job_empty_allowed_user_ids_returns_empty() -> None:
    repo = PostgresMatchRepository("postgresql://fake/fake")
    assert repo.find_users_for_job([0.1] * 384, job_id=1, threshold=0.5, allowed_user_ids=[]) == []


def test_find_jobs_for_user_invalid_embedding_returns_empty() -> None:
    repo = PostgresMatchRepository("postgresql://fake/fake")
    assert repo.find_jobs_for_user(None, user_id=1, threshold=0.5) == []  # type: ignore[arg-type]
    assert repo.find_jobs_for_user([], user_id=1, threshold=0.5) == []
    assert repo.find_jobs_for_user([0.1] * 10, user_id=1, threshold=0.5) == []


def test_find_jobs_for_user_maps_rows() -> None:
    repo = PostgresMatchRepository("postgresql://fake/fake")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [{"job_id": 77, "similarity": 0.8123}]

    @contextlib.contextmanager
    def fake_conn():
        yield conn

    repo._conn = fake_conn  # type: ignore[method-assign]

    got = repo.find_jobs_for_user([0.1] * 384, user_id=5, threshold=0.7, limit=3, days_back=14)

    assert len(got) == 1
    assert got[0].user_id == 5
    assert got[0].job_id == 77
    assert got[0].match_score == 0.8123
    assert got[0].raw_similarity == 0.8123
    assert got[0].final_score == 0.8123
    sql, params = cur.execute.call_args.args
    assert "FROM jobs j" in sql
    assert params[1] == 14
    assert params[2] == 0.7
    assert params[3] == 5
    assert params[4] == 3
