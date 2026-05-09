"""Helpers for loading PostgreSQL pool settings from environment."""

from __future__ import annotations

import os

POOL_MIN_ENV = "PG_POOL_MIN_CONNS"
POOL_MAX_ENV = "PG_POOL_MAX_CONNS"
STATEMENT_TIMEOUT_ENV = "PG_STATEMENT_TIMEOUT_MS"

DEFAULT_POOL_MIN_CONNS = 0
DEFAULT_POOL_MAX_CONNS = 2
DEFAULT_STATEMENT_TIMEOUT_MS = 10_000


def load_postgres_pool_settings() -> dict[str, int]:
    """Return validated pool kwargs for PostgreSQL repositories."""
    minconn = _parse_int_env(POOL_MIN_ENV, DEFAULT_POOL_MIN_CONNS)
    maxconn = _parse_int_env(POOL_MAX_ENV, DEFAULT_POOL_MAX_CONNS)
    statement_timeout_ms = _parse_int_env(STATEMENT_TIMEOUT_ENV, DEFAULT_STATEMENT_TIMEOUT_MS)

    if minconn < 0:
        raise ValueError(f"{POOL_MIN_ENV} must be >= 0")
    if maxconn < minconn:
        raise ValueError(f"{POOL_MAX_ENV} must be >= {POOL_MIN_ENV}")
    if statement_timeout_ms < 0:
        raise ValueError(f"{STATEMENT_TIMEOUT_ENV} must be >= 0")

    return {
        "minconn": minconn,
        "maxconn": maxconn,
        "statement_timeout_ms": statement_timeout_ms,
    }


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
