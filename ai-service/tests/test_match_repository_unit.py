"""Unit-тесты PostgresMatchRepository без БД."""

from __future__ import annotations

import builtins
import contextlib
import importlib
import sys
from unittest.mock import MagicMock

from ai_service.adapter.postgres import PostgresMatchRepository


def test_postgres_repository_modules_import_without_sentence_transformers(
    monkeypatch,
) -> None:
    # Restore the `postgres` attribute on the parent package at teardown.
    # When sys.modules["ai_service.adapter.postgres"] is evicted and the package
    # is re-imported, Python sets a NEW module object as the `postgres` attribute
    # on `ai_service.adapter`.  monkeypatch only restores sys.modules, so without
    # this line subsequent tests that walk the attribute chain get the stale NEW
    # module which is missing dynamically-imported submodule attrs.
    import ai_service.adapter as _adapter_pkg
    monkeypatch.setattr(_adapter_pkg, "postgres", _adapter_pkg.postgres)

    for module_name in [
        "ai_service.adapter.postgres",
        "ai_service.adapter.postgres.repository",
        "ai_service.adapter.postgres.match_repository",
        "ai_service.adapter.sentence_transformers",
        "ai_service.adapter.sentence_transformers.embedding",
    ]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    attempted_imports: list[str] = []
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sentence_transformers" or name.startswith("sentence_transformers."):
            attempted_imports.append(name)
            raise AssertionError(f"unexpected import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    repo_module = importlib.import_module("ai_service.adapter.postgres.repository")
    match_module = importlib.import_module("ai_service.adapter.postgres.match_repository")

    assert repo_module.EMBEDDING_DIM == 384
    assert match_module.EMBEDDING_DIM == 384
    assert attempted_imports == []


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
    assert "j.status = 'active'" in sql
    assert "j.source <> 'kwork' OR j.last_seen_at >= NOW() - INTERVAL '6 hours'" in sql
    assert params[1] == 14
    assert params[2] == 0.7
    assert params[3] == 5
    assert params[4] == 3


def test_find_users_for_job_joins_active_job() -> None:
    repo = PostgresMatchRepository("postgresql://fake/fake")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [{"user_id": 11, "profile_text": "hello", "similarity": 0.91}]

    @contextlib.contextmanager
    def fake_conn():
        yield conn

    repo._conn = fake_conn  # type: ignore[method-assign]

    got = repo.find_users_for_job([0.1] * 384, job_id=7, threshold=0.8, limit=4)

    assert len(got) == 1
    assert got[0].user_id == 11
    assert got[0].job_id == 7
    sql, params = cur.execute.call_args.args
    assert "JOIN jobs j ON j.id = %s" in sql
    assert "j.status = 'active'" in sql
    assert "j.source <> 'kwork' OR j.last_seen_at >= NOW() - INTERVAL '6 hours'" in sql
    assert "COALESCE(j.posted_at, j.created_at) >= NOW() - make_interval(days => %s)" not in sql
    assert "WHERE n.job_id = j.id" in sql
    assert params[3] == 7
    assert params[4] == 0.8
    assert params[5] == 4


def test_find_users_for_job_applies_explicit_age_gate() -> None:
    repo = PostgresMatchRepository("postgresql://fake/fake")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value = [{"user_id": 11, "profile_text": "hello", "similarity": 0.91}]

    @contextlib.contextmanager
    def fake_conn():
        yield conn

    repo._conn = fake_conn  # type: ignore[method-assign]

    repo.find_users_for_job([0.1] * 384, job_id=7, threshold=0.8, limit=4, max_age_days=2)

    sql, params = cur.execute.call_args.args
    assert "COALESCE(j.posted_at, j.created_at) >= NOW() - make_interval(days => %s)" in sql
    assert params[5] == 2
    assert params[6] == 4
