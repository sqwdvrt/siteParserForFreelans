from __future__ import annotations

from ai_service.adapter.postgres._pooled_repository import _sanitize_dsn_for_psycopg2


def test_sanitize_dsn_removes_pgx_only_exec_mode_param() -> None:
    dsn = "postgresql://u:p@host:6543/db?sslmode=require&default_query_exec_mode=simple_protocol"
    got = _sanitize_dsn_for_psycopg2(dsn)
    assert "default_query_exec_mode" not in got
    assert "sslmode=require" in got


def test_sanitize_dsn_keeps_untouched_when_param_missing() -> None:
    dsn = "postgresql://u:p@host:6543/db?sslmode=require"
    assert _sanitize_dsn_for_psycopg2(dsn) == dsn


def test_sanitize_dsn_ignores_non_url_string() -> None:
    dsn = "dbname=mydb host=localhost user=me"
    assert _sanitize_dsn_for_psycopg2(dsn) == dsn
