from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import pytest


def test_sign_user_request_is_deterministic(bot):
    sig = bot._sign_user_request(
        "secret123456789012345678901234567890",
        "POST",
        "/users",
        123,
        "1700000000",
        "nonce1234567890abcd",
        b'{"telegram_id":123}',
    )
    assert len(sig) == 64
    assert sig == bot._sign_user_request(
        "secret123456789012345678901234567890",
        "POST",
        "/users",
        123,
        "1700000000",
        "nonce1234567890abcd",
        b'{"telegram_id":123}',
    )


def test_signed_user_headers_contains_auth_and_signature(bot, monkeypatch):
    monkeypatch.setattr(bot.time, "time", lambda: 1700000000)
    monkeypatch.setattr(bot.secrets, "token_hex", lambda _: "abcd" * 8)
    headers = bot._signed_user_headers(
        "api_token_abcdefghijklmnopqrstuvwxyz123456",
        "hmac_secret_abcdefghijklmnopqrstuvwxyz123456",
        "POST",
        "https://api.example.com/users",
        123456789,
        b'{"telegram_id":123456789}',
    )
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["X-Telegram-ID"] == "123456789"
    assert headers["X-Request-Timestamp"] == "1700000000"
    assert len(headers["X-Request-Signature"]) == 64


def test_post_users_success(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_post", lambda *args, **kwargs: (200, {"user_id": 77}))
    assert bot.post_users("https://api.example.com", 123, "tok", "hmac") == 77


def test_post_users_failure_returns_none(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_post", lambda *args, **kwargs: (503, None))
    assert bot.post_users("https://api.example.com", 123, "tok", "hmac") is None


def test_put_user_profile_success(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_put", lambda *args, **kwargs: 204)
    assert bot.put_user_profile("https://api.example.com", 1, 123, "python", "tok", "hmac")


def test_put_user_profile_failure(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_put", lambda *args, **kwargs: 500)
    assert not bot.put_user_profile("https://api.example.com", 1, 123, "python", "tok", "hmac")


def test_put_user_profile_status_returns_status_code(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_put", lambda *args, **kwargs: 400)
    assert bot.put_user_profile_status("https://api.example.com", 1, 123, "python", "tok", "hmac") == 400


def test_get_user_is_pro_reads_flag_from_preferences(bot, monkeypatch):
    monkeypatch.setattr(bot, "get_user_preferences", lambda *args, **kwargs: {"is_pro": True})
    assert bot.get_user_is_pro("https://api.example.com", 1, 123, "tok", "hmac") is True


def test_get_user_is_pro_returns_none_when_flag_missing(bot, monkeypatch):
    monkeypatch.setattr(bot, "get_user_preferences", lambda *args, **kwargs: {"preferred_sources": []})
    assert bot.get_user_is_pro("https://api.example.com", 1, 123, "tok", "hmac") is None


def test_get_user_stats_returns_payload(bot, monkeypatch):
    monkeypatch.setattr(
        bot,
        "_http_get",
        lambda *args, **kwargs: {
            "period_days": 7,
            "projects_found": 42,
            "projects_shown": 12,
            "projects_filtered_other": 30,
            "projects_filtered_by_budget": 18,
            "budget_filter_active": True,
        },
    )
    stats = bot.get_user_stats("https://api.example.com", 1, 123, "tok", "hmac")
    assert isinstance(stats, dict)
    assert stats["projects_found"] == 42


def test_get_user_stats_returns_none_on_failure(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *args, **kwargs: None)
    assert bot.get_user_stats("https://api.example.com", 1, 123, "tok", "hmac") is None


def test_get_updates_returns_next_offset(bot, monkeypatch):
    monkeypatch.setattr(
        bot,
        "_http_get",
        lambda *args, **kwargs: {"ok": True, "result": [{"update_id": 4}, {"update_id": 5}]},
    )
    updates, next_offset = bot.get_updates("token", offset=1)
    assert len(updates) == 2
    assert next_offset == 6


def test_get_updates_returns_empty_when_not_ok(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *args, **kwargs: {"ok": False})
    updates, next_offset = bot.get_updates("token", offset=42)
    assert updates == []
    assert next_offset == 42


def test_get_updates_returns_status_when_requested(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda *args, **kwargs: None)
    updates, next_offset, ok = bot.get_updates("token", offset=7, include_status=True)
    assert updates == []
    assert next_offset == 7
    assert ok is False


def test_send_message_returns_false_on_error_status(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_post", lambda *args, **kwargs: (500, None))
    assert not bot.send_message("token", 123, "hello")


def test_send_message_returns_true_on_200(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_post", lambda *args, **kwargs: (200, {"ok": True}))
    assert bot.send_message("token", 123, "hello")


def test_send_message_omits_parse_mode_by_default(bot, monkeypatch):
    captured = {}

    def fake_http_post(url, data, headers=None):
        _ = url
        _ = headers
        captured["data"] = data
        return 200, {"ok": True}

    monkeypatch.setattr(bot, "_http_post", fake_http_post)

    assert bot.send_message("token", 123, "hello")
    assert "parse_mode" not in captured["data"]


def test_send_message_can_opt_in_to_html(bot, monkeypatch):
    captured = {}

    def fake_http_post(url, data, headers=None):
        _ = url
        _ = headers
        captured["data"] = data
        return 200, {"ok": True}

    monkeypatch.setattr(bot, "_http_post", fake_http_post)

    assert bot.send_message("token", 123, "<b>hello</b>", parse_html=True)
    assert captured["data"]["parse_mode"] == "HTML"


def test_safe_open_uses_http_only_opener(bot, monkeypatch):
    called = {}

    def fake_open(url_or_request, timeout):
        called["args"] = (url_or_request, timeout)
        return "ok"

    monkeypatch.setattr(bot._HTTP_ONLY_OPENER, "open", fake_open)
    req = object()
    assert bot._safe_open(req, timeout=7) == "ok"
    assert called["args"] == (req, 7)


def test_safe_redirect_handler_blocks_disallowed_host(bot):
    bot._register_allowed_host("https://api.example.com")
    handler = bot._SafeRedirectHandler()
    req = urllib.request.Request("https://api.example.com/start")

    with pytest.raises(urllib.error.HTTPError) as exc:
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/collect")

    assert exc.value.code == 403


def test_safe_redirect_handler_allows_relative_redirect_on_allowed_host(bot):
    bot._register_allowed_host("https://api.example.com")
    handler = bot._SafeRedirectHandler()
    req = urllib.request.Request("https://api.example.com/start")

    redirected = handler.redirect_request(req, None, 302, "Found", {}, "/next")

    assert redirected is not None
    assert urllib.parse.urlsplit(redirected.full_url).hostname == "api.example.com"


def test_post_feedback_signs_with_telegram_id_not_user_id(bot, monkeypatch):
    """post_feedback должен подписывать запрос с telegram_id, а не user_id."""
    captured = {}

    def fake_http_post(url, data, headers=None):
        captured["headers"] = headers or {}
        return (204, None)

    monkeypatch.setattr(bot, "_http_post", fake_http_post)
    bot._register_allowed_host("https://api.example.com")

    user_id = 42          # database user ID
    telegram_id = 987654  # Telegram user ID — different from user_id

    bot.post_feedback("https://api.example.com", user_id, telegram_id, 1, "good", "tok", "secret")

    assert "X-Telegram-ID" in captured["headers"]
    assert captured["headers"]["X-Telegram-ID"] == str(telegram_id), (
        f"X-Telegram-ID must be telegram_id ({telegram_id}), got {captured['headers']['X-Telegram-ID']}"
    )
    assert captured["headers"]["X-Telegram-ID"] != str(user_id), (
        "X-Telegram-ID must NOT be user_id"
    )


def test_post_feedback_url_contains_user_id(bot, monkeypatch):
    """URL запроса должен содержать user_id (database ID), не telegram_id."""
    captured = {}

    def fake_http_post(url, data, headers=None):
        captured["url"] = url
        return (204, None)

    monkeypatch.setattr(bot, "_http_post", fake_http_post)
    bot._register_allowed_host("https://api.example.com")

    user_id = 42
    telegram_id = 987654

    bot.post_feedback("https://api.example.com", user_id, telegram_id, 1, "good", "tok", "secret")

    assert f"/users/{user_id}/feedback" in captured["url"]
    assert str(telegram_id) not in captured["url"]


def test_handle_callback_calls_post_feedback_with_correct_ids(bot, monkeypatch):
    """handle_callback должен передавать правильные user_id и telegram_id в post_feedback."""
    calls = []

    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: 42)
    monkeypatch.setattr(bot, "post_feedback", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: None)

    callback = {
        "id": "cb1",
        "data": "fb:g:7",
        "from": {"id": 987654},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert len(calls) == 1
    _api_url, called_user_id, called_telegram_id, job_id, feedback = calls[0][:5]
    assert called_user_id == 42, f"user_id должен быть database ID (42), получили {called_user_id}"
    assert called_telegram_id == 987654, f"telegram_id должен быть Telegram ID (987654), получили {called_telegram_id}"
    assert job_id == 7
    assert feedback == "good"


def test_handle_callback_manual_onboarding_switches_to_edit_mode(bot, monkeypatch):
    edits = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: edits.append(a))

    callback = {
        "id": "cb2",
        "data": "ob:manual",
        "from": {"id": 987654},
        "message": {
            "chat": {"id": 123},
            "message_id": 55,
        },
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert bot._parse_onboarding_state(bot._get_conversation_state(987654)) == {"step": "edit"}
    assert edits
    assert "Отправьте текст профиля" in edits[0][3]


def test_render_metrics_contains_counters_and_ready_gauge(bot):
    bot._METRICS.inc("telegram_bot_updates_total", transport="polling", result="handled")
    bot._METRICS.inc("telegram_bot_commands_total", command="start", result="ok")
    bot._METRICS.set_ready(True)

    payload = bot._render_metrics()

    assert "telegram_bot_updates_total{result=\"handled\",transport=\"polling\"} 1" in payload
    assert "telegram_bot_commands_total{command=\"start\",result=\"ok\"} 1" in payload
    assert "telegram_bot_ready 1" in payload
