"""Transport security checks for production environment."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import parse_qs, urlsplit


def is_production_env(raw: str | None) -> bool:
    value = (raw or "").strip().lower()
    return value in {"prod", "production"}


def validate_postgres_tls_for_production(name: str, db_url: str) -> None:
    parsed = urlsplit((db_url or "").strip())
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"{name} must use postgres:// or postgresql:// scheme")
    sslmode = (parse_qs(parsed.query).get("sslmode", [""])[0] or "").strip().lower()
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise ValueError(
            f"{name} must use sslmode=require (or verify-ca/verify-full) in production"
        )


def validate_redis_tls_for_production(name: str, redis_url: str) -> None:
    parsed = urlsplit((redis_url or "").strip())
    hostname = parsed.hostname or ""
    is_internal_host = _is_internal_redis_host(hostname)

    if parsed.scheme != "rediss" and not is_internal_host:
        raise ValueError(f"{name} must use rediss:// in production")
    if not parsed.password:
        raise ValueError(f"{name} must include password in production")


def _is_internal_redis_host(hostname: str) -> bool:
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        address = ip_address(hostname)
    except ValueError:
        return "." not in hostname
    return address.is_loopback or (address.version == 4 and address.is_private)
