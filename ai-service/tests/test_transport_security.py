from __future__ import annotations

import pytest

from ai_service.util.transport_security import (
    is_production_env,
    validate_postgres_tls_for_production,
    validate_redis_tls_for_production,
)


def test_is_production_env_true_variants() -> None:
    assert is_production_env("prod")
    assert is_production_env("production")
    assert is_production_env(" Production ")


def test_is_production_env_false_variants() -> None:
    assert not is_production_env(None)
    assert not is_production_env("")
    assert not is_production_env("development")


def test_validate_postgres_tls_accepts_secure_sslmode() -> None:
    validate_postgres_tls_for_production(
        "DATABASE_URL",
        "postgresql://u:p@db.example.com:5432/app?sslmode=require",
    )
    validate_postgres_tls_for_production(
        "DATABASE_URL",
        "postgres://u:p@db.example.com:5432/app?sslmode=verify-full",
    )


def test_validate_postgres_tls_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError):
        validate_postgres_tls_for_production(
            "DATABASE_URL",
            "mysql://u:p@db.example.com:3306/app",
        )


def test_validate_postgres_tls_rejects_insecure_sslmode() -> None:
    with pytest.raises(ValueError):
        validate_postgres_tls_for_production(
            "DATABASE_URL",
            "postgresql://u:p@db.example.com:5432/app?sslmode=disable",
        )


def test_validate_redis_tls_accepts_rediss_with_password() -> None:
    validate_redis_tls_for_production(
        "REDIS_URL",
        "rediss://default:strong_password@redis.example.com:6380/0",
    )


def test_validate_redis_tls_rejects_non_tls_scheme() -> None:
    with pytest.raises(ValueError):
        validate_redis_tls_for_production(
            "REDIS_URL",
            "redis://default:strong_password@redis.example.com:6379/0",
        )


def test_validate_redis_tls_rejects_missing_password() -> None:
    with pytest.raises(ValueError):
        validate_redis_tls_for_production(
            "REDIS_URL",
            "rediss://redis.example.com:6380/0",
        )
