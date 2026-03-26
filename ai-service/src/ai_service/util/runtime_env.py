from __future__ import annotations

from ai_service.util.transport_security import is_production_env


def require_env(name: str, value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{name} not set")
    return normalized


def resolve_redis_url(app_env: str, value: str | None, *, default_dev_url: str = "redis://localhost:6379/0") -> str:
    normalized = (value or "").strip()
    if normalized:
        return normalized
    if is_production_env(app_env):
        raise ValueError("REDIS_URL not set")
    return default_dev_url


def resolve_llm_provider(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        raise ValueError("LLM_PROVIDER not set")
    if normalized != "gemini":
        raise ValueError("LLM_PROVIDER must be 'gemini'")
    return normalized
