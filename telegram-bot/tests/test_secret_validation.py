from __future__ import annotations

import pytest


def test_validate_secret_rejects_placeholder_prefix(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "replace_with_secret_value_1234567890", 32)


def test_validate_secret_rejects_short_value(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "short", 32)


def test_validate_secret_rejects_empty_value(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "", 32)


def test_validate_secret_rejects_low_entropy(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "A" * 40, 32)


def test_validate_secret_accepts_strong_value(bot):
    bot._validate_secret("API_AUTH_TOKEN", "d8xQ9mK2vP6sR0nC4tY7wB1hF5jL3zUa", 32)


def test_shannon_entropy_empty_secret(bot):
    assert bot._shannon_entropy_bits("") == 0.0


def test_parse_log_level_accepts_valid_value(bot):
    level, ok = bot._parse_log_level("debug")
    assert ok is True
    assert level == bot.logging.DEBUG


def test_parse_log_level_fallback_on_invalid_value(bot):
    level, ok = bot._parse_log_level("totally-invalid", bot.logging.INFO)
    assert ok is False
    assert level == bot.logging.INFO


def test_validate_api_url_for_production_requires_https(bot):
    with pytest.raises(ValueError):
        bot._validate_api_url_for_production("http://api.example.com")


def test_validate_api_url_for_production_requires_host(bot):
    with pytest.raises(ValueError):
        bot._validate_api_url_for_production("https:///path")


def test_register_allowed_host_allows_custom_api_host(bot):
    with pytest.raises(ValueError):
        bot._validate_outbound_url("https://api.example.com/users")

    bot._register_allowed_host("https://api.example.com")
    bot._validate_outbound_url("https://api.example.com/users")


def test_validate_outbound_url_rejects_invalid_scheme(bot):
    with pytest.raises(ValueError):
        bot._validate_outbound_url("ftp://api.telegram.org/x")


def test_validate_outbound_url_rejects_missing_host(bot):
    with pytest.raises(ValueError):
        bot._validate_outbound_url("https:///path")
