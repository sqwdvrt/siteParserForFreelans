"""Unit-тесты PostgresMatchRepository без БД."""

from __future__ import annotations

from ai_service.adapter.postgres import PostgresMatchRepository


def test_invalid_embedding_returns_empty() -> None:
    """При None, пустом или len≠384 — возвращает [] без обращения к БД."""
    repo = PostgresMatchRepository("postgresql://fake/fake")
    job_id = 1
    assert repo.find_users_for_job(None, job_id, 0.5) == []  # type: ignore[arg-type]
    assert repo.find_users_for_job([], job_id, 0.5) == []
    assert repo.find_users_for_job([0.1] * 10, job_id, 0.5) == []
