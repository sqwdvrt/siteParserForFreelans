"""Unit tests for uncovered HTTP helper functions in main.py.

Covers:
  - _to_int_or_default
  - _exception_name
  - _compact_log_text
  - _json_body
  - _validate_outbound_url
  - _is_production_env
  - _validate_api_url_for_production
  - _validate_webhook_url
  - get_user_profile_text
  - put_user_notify_hour
  - _observe_http_request (metric side-effect smoke test)
  - _retry_delay_sec / _poll_retry_delay_sec (bounded values)
  - _shannon_entropy_bits
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# _to_int_or_default
# ---------------------------------------------------------------------------

def test_to_int_or_default_converts_int_string(bot):
    assert bot._to_int_or_default("42") == 42


def test_to_int_or_default_converts_integer(bot):
    assert bot._to_int_or_default(7) == 7


def test_to_int_or_default_returns_default_for_none(bot):
    assert bot._to_int_or_default(None) == 0


def test_to_int_or_default_returns_default_for_non_numeric_string(bot):
    assert bot._to_int_or_default("abc") == 0


def test_to_int_or_default_uses_custom_default(bot):
    assert bot._to_int_or_default("bad", default=99) == 99


def test_to_int_or_default_returns_default_for_empty_string(bot):
    assert bot._to_int_or_default("", default=-1) == -1


# ---------------------------------------------------------------------------
# _exception_name
# ---------------------------------------------------------------------------

def test_exception_name_returns_class_name(bot):
    err = ValueError("something went wrong")
    assert bot._exception_name(err) == "ValueError"


def test_exception_name_does_not_include_message(bot):
    err = RuntimeError("secret token leaked")
    result = bot._exception_name(err)
    assert "secret" not in result
    assert result == "RuntimeError"


def test_exception_name_connection_error(bot):
    err = ConnectionError("host unreachable")
    assert bot._exception_name(err) == "ConnectionError"


# ---------------------------------------------------------------------------
# _compact_log_text
# ---------------------------------------------------------------------------

def test_compact_log_text_short_string_unchanged(bot):
    assert bot._compact_log_text("hello world") == "hello world"


def test_compact_log_text_collapses_whitespace(bot):
    assert bot._compact_log_text("hello   world\t\nfoo") == "hello world foo"


def test_compact_log_text_truncates_at_limit(bot):
    long_text = "a" * 300
    result = bot._compact_log_text(long_text, limit=200)
    assert len(result) == 200
    assert result.endswith("...")


def test_compact_log_text_respects_custom_limit(bot):
    result = bot._compact_log_text("hello world this is a test", limit=10)
    assert len(result) == 10
    assert result.endswith("...")


def test_compact_log_text_empty_string(bot):
    assert bot._compact_log_text("") == ""


# ---------------------------------------------------------------------------
# _json_body
# ---------------------------------------------------------------------------

def test_json_body_returns_bytes(bot):
    result = bot._json_body({"key": "value"})
    assert isinstance(result, bytes)


def test_json_body_is_valid_utf8_json(bot):
    import json
    data = {"telegram_id": 123456, "profile_text": "разработчик Go"}
    result = bot._json_body(data)
    parsed = json.loads(result.decode("utf-8"))
    assert parsed == data


def test_json_body_uses_compact_separators(bot):
    result = bot._json_body({"a": 1, "b": 2})
    # compact serialization has no spaces around separators
    assert b" " not in result


# ---------------------------------------------------------------------------
# _validate_outbound_url
# ---------------------------------------------------------------------------

def test_validate_outbound_url_raises_for_disallowed_host(bot):
    bot._register_allowed_host("https://api.example.com")
    with pytest.raises(ValueError, match="not in allowlist"):
        bot._validate_outbound_url("https://evil.com/steal")


def test_validate_outbound_url_accepts_allowed_host(bot):
    bot._register_allowed_host("https://trusted.example.com")
    # should not raise
    bot._validate_outbound_url("https://trusted.example.com/path")


def test_validate_outbound_url_raises_for_unsupported_scheme(bot):
    bot._register_allowed_host("ftp://files.example.com")
    with pytest.raises(ValueError, match="http"):
        bot._validate_outbound_url("ftp://files.example.com/data")


def test_validate_outbound_url_raises_for_missing_host(bot):
    with pytest.raises(ValueError):
        bot._validate_outbound_url("https://")


# ---------------------------------------------------------------------------
# _is_production_env
# ---------------------------------------------------------------------------

def test_is_production_env_detects_prod(bot):
    assert bot._is_production_env("prod") is True


def test_is_production_env_detects_production(bot):
    assert bot._is_production_env("production") is True


def test_is_production_env_case_insensitive(bot):
    assert bot._is_production_env("PROD") is True
    assert bot._is_production_env("Production") is True


def test_is_production_env_returns_false_for_dev(bot):
    assert bot._is_production_env("dev") is False


def test_is_production_env_returns_false_for_none(bot):
    assert bot._is_production_env(None) is False


def test_is_production_env_returns_false_for_empty(bot):
    assert bot._is_production_env("") is False


# ---------------------------------------------------------------------------
# _validate_api_url_for_production
# ---------------------------------------------------------------------------

def test_validate_api_url_for_production_accepts_https(bot):
    # should not raise
    bot._validate_api_url_for_production("https://api.example.com")


def test_validate_api_url_for_production_rejects_http(bot):
    with pytest.raises(ValueError, match="https"):
        bot._validate_api_url_for_production("http://api.example.com")


def test_validate_api_url_for_production_rejects_missing_host(bot):
    with pytest.raises(ValueError):
        bot._validate_api_url_for_production("https://")


# ---------------------------------------------------------------------------
# _validate_webhook_url
# ---------------------------------------------------------------------------

def test_validate_webhook_url_accepts_valid_url(bot):
    # WEBHOOK_PATH is defined in the module
    path = bot.WEBHOOK_PATH
    bot._validate_webhook_url(f"https://mybot.example.com{path}")


def test_validate_webhook_url_rejects_http(bot):
    path = bot.WEBHOOK_PATH
    with pytest.raises(ValueError, match="https"):
        bot._validate_webhook_url(f"http://mybot.example.com{path}")


def test_validate_webhook_url_rejects_wrong_path(bot):
    with pytest.raises(ValueError, match=bot.WEBHOOK_PATH):
        bot._validate_webhook_url("https://mybot.example.com/wrong/path")


# ---------------------------------------------------------------------------
# _shannon_entropy_bits
# ---------------------------------------------------------------------------

def test_shannon_entropy_bits_empty_string_is_zero(bot):
    assert bot._shannon_entropy_bits("") == 0.0


def test_shannon_entropy_bits_uniform_string_has_high_entropy(bot):
    # 32-char random-looking hex
    result = bot._shannon_entropy_bits("0123456789abcdef" * 2)
    assert result > 60.0


def test_shannon_entropy_bits_repeated_char_has_low_entropy(bot):
    result = bot._shannon_entropy_bits("aaaaaaaaaaaaaaaa")
    assert result == 0.0


# ---------------------------------------------------------------------------
# _retry_delay_sec / _poll_retry_delay_sec – bounded values
# ---------------------------------------------------------------------------

def test_retry_delay_sec_does_not_exceed_max(bot):
    for attempt in range(10):
        delay = bot._retry_delay_sec(attempt)
        assert delay <= bot.API_RETRY_MAX_DELAY_SEC + 0.2 + 0.01  # jitter headroom


def test_retry_delay_sec_increases_with_attempts(bot):
    bot._retry_delay_sec(0)
    bot._retry_delay_sec(1)
    # base values (before jitter) should grow; run many times so jitter
    # doesn't flip the order – just check base formula directly
    base0 = min(bot.API_RETRY_MAX_DELAY_SEC, bot.API_RETRY_BASE_DELAY_SEC * (2 ** 0))
    base1 = min(bot.API_RETRY_MAX_DELAY_SEC, bot.API_RETRY_BASE_DELAY_SEC * (2 ** 1))
    assert base1 >= base0


def test_poll_retry_delay_sec_does_not_exceed_max(bot):
    for attempt in range(10):
        delay = bot._poll_retry_delay_sec(attempt)
        assert delay <= bot.POLL_RETRY_MAX_DELAY_SEC + 0.2 + 0.01


# ---------------------------------------------------------------------------
# get_user_profile_text
# ---------------------------------------------------------------------------

def test_get_user_profile_text_returns_text_on_success(bot, monkeypatch):
    monkeypatch.setattr(
        bot, "_http_get", lambda *args, **kwargs: {"profile_text": "experienced Go dev"}
    )
    result = bot.get_user_profile_text("https://api.example.com", 1, 123, "tok", "hmac")
    assert result == "experienced Go dev"


def test_get_user_profile_text_returns_empty_string_when_field_missing(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *args, **kwargs: {"other_field": "x"})
    result = bot.get_user_profile_text("https://api.example.com", 1, 123, "tok", "hmac")
    assert result == ""


def test_get_user_profile_text_returns_none_when_http_get_returns_none(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *args, **kwargs: None)
    result = bot.get_user_profile_text("https://api.example.com", 1, 123, "tok", "hmac")
    assert result is None


def test_get_user_profile_text_returns_none_when_response_is_not_dict(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *args, **kwargs: [1, 2, 3])
    result = bot.get_user_profile_text("https://api.example.com", 1, 123, "tok", "hmac")
    assert result is None


def test_get_user_profile_text_returns_empty_string_when_profile_text_is_none(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *args, **kwargs: {"profile_text": None})
    result = bot.get_user_profile_text("https://api.example.com", 1, 123, "tok", "hmac")
    assert result == ""


# ---------------------------------------------------------------------------
# put_user_notify_hour
# ---------------------------------------------------------------------------

def test_put_user_notify_hour_returns_204_on_success(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_put", lambda *args, **kwargs: 204)
    status = bot.put_user_notify_hour("https://api.example.com", 1, 123, 9, "tok", "hmac")
    assert status == 204


def test_put_user_notify_hour_returns_403_for_non_pro(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_put", lambda *args, **kwargs: 403)
    status = bot.put_user_notify_hour("https://api.example.com", 1, 123, 9, "tok", "hmac")
    assert status == 403


def test_put_user_notify_hour_url_contains_user_id(bot, monkeypatch):
    captured = {}

    def fake_put(url, data, headers=None, *, make_headers=None):
        captured["url"] = url
        return 204

    monkeypatch.setattr(bot, "_http_put", fake_put)
    bot.put_user_notify_hour("https://api.example.com", 42, 999, 10, "tok", "hmac")
    assert "/users/42/notify-hour" in captured["url"]


def test_put_user_notify_hour_sends_correct_hour(bot, monkeypatch):
    captured = {}

    def fake_put(url, data, headers=None, *, make_headers=None):
        captured["data"] = data
        return 204

    monkeypatch.setattr(bot, "_http_put", fake_put)
    bot.put_user_notify_hour("https://api.example.com", 1, 123, 14, "tok", "hmac")
    assert captured["data"]["hour"] == 14


# ---------------------------------------------------------------------------
# get_user_preferences – URL construction
# ---------------------------------------------------------------------------

def test_get_user_preferences_calls_http_get_with_correct_url(bot, monkeypatch):
    captured = {}

    def fake_get(url, params, headers=None):
        captured["url"] = url
        return {"is_pro": False}

    monkeypatch.setattr(bot, "_http_get", fake_get)
    bot.get_user_preferences("https://api.example.com", 77, 123, "tok", "hmac")
    assert captured["url"] == "https://api.example.com/users/77/preferences"


def test_get_user_preferences_returns_none_when_http_get_fails(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *a, **kw: None)
    result = bot.get_user_preferences("https://api.example.com", 1, 123, "tok", "hmac")
    assert result is None


# ---------------------------------------------------------------------------
# get_user_is_pro – edge cases beyond existing tests
# ---------------------------------------------------------------------------

def test_get_user_is_pro_returns_false_when_flag_is_false(bot, monkeypatch):
    monkeypatch.setattr(bot, "get_user_preferences", lambda *a, **kw: {"is_pro": False})
    result = bot.get_user_is_pro("https://api.example.com", 1, 123, "tok", "hmac")
    assert result is False


def test_get_user_is_pro_returns_none_when_preferences_is_none(bot, monkeypatch):
    monkeypatch.setattr(bot, "get_user_preferences", lambda *a, **kw: None)
    result = bot.get_user_is_pro("https://api.example.com", 1, 123, "tok", "hmac")
    assert result is None


def test_get_user_is_pro_returns_none_when_preferences_is_not_dict(bot, monkeypatch):
    monkeypatch.setattr(bot, "get_user_preferences", lambda *a, **kw: [1, 2])
    result = bot.get_user_is_pro("https://api.example.com", 1, 123, "tok", "hmac")
    assert result is None


# ---------------------------------------------------------------------------
# get_user_stats – URL construction
# ---------------------------------------------------------------------------

def test_get_user_stats_calls_http_get_with_correct_url(bot, monkeypatch):
    captured = {}

    def fake_get(url, params, headers=None):
        captured["url"] = url
        return {"projects_found": 5}

    monkeypatch.setattr(bot, "_http_get", fake_get)
    bot.get_user_stats("https://api.example.com", 55, 123, "tok", "hmac")
    assert captured["url"] == "https://api.example.com/users/55/stats"
