from __future__ import annotations

import pytest

from ai_service.util.postgres_pool_config import load_postgres_pool_settings


def test_load_postgres_pool_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_POOL_MIN_CONNS", raising=False)
    monkeypatch.delenv("PG_POOL_MAX_CONNS", raising=False)
    monkeypatch.delenv("PG_STATEMENT_TIMEOUT_MS", raising=False)

    assert load_postgres_pool_settings() == {
        "minconn": 0,
        "maxconn": 2,
        "statement_timeout_ms": 10_000,
    }


def test_load_postgres_pool_settings_validates_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_POOL_MIN_CONNS", "2")
    monkeypatch.setenv("PG_POOL_MAX_CONNS", "1")

    with pytest.raises(ValueError, match="PG_POOL_MAX_CONNS"):
        load_postgres_pool_settings()
