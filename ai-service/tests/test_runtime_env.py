from __future__ import annotations

import pytest

from ai_service.util.runtime_env import require_env, resolve_llm_provider, resolve_redis_url


def test_require_env_rejects_empty() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL not set"):
        require_env("DATABASE_URL", " ")


def test_require_env_returns_trimmed_value() -> None:
    assert require_env("DATABASE_URL", "  postgres://user:pass@host/db  ") == "postgres://user:pass@host/db"


def test_resolve_redis_url_requires_value_in_production() -> None:
    with pytest.raises(ValueError, match="REDIS_URL not set"):
        resolve_redis_url("production", None)


def test_resolve_redis_url_uses_dev_default_outside_production() -> None:
    assert resolve_redis_url("development", None) == "redis://localhost:6379/0"


def test_resolve_llm_provider_requires_known_value() -> None:
    with pytest.raises(ValueError, match="LLM_PROVIDER not set"):
        resolve_llm_provider(None)

    with pytest.raises(ValueError, match="LLM_PROVIDER not set"):
        resolve_llm_provider("")

    with pytest.raises(ValueError, match="LLM_PROVIDER must be 'gemini' or 'ollama'"):
        resolve_llm_provider("claude")

    assert resolve_llm_provider(" Gemini ") == "gemini"
