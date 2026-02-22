from __future__ import annotations

import importlib
import io
import json
import urllib.parse
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import pytest


def _load_bot_module():
    mod = importlib.import_module("main")
    return importlib.reload(mod)


@pytest.fixture
def bot():
    return _load_bot_module()


def test_validate_secret_rejects_placeholder_prefix(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "replace_with_secret_value_1234567890", 32)


def test_validate_secret_rejects_short_value(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "short", 32)


def test_validate_secret_accepts_strong_value(bot):
    bot._validate_secret("API_AUTH_TOKEN", "d8xQ9mK2vP6sR0nC4tY7wB1hF5jL3zUa", 32)


def test_validate_api_url_for_production_requires_https(bot):
    with pytest.raises(ValueError):
        bot._validate_api_url_for_production("http://api.example.com")


def test_register_allowed_host_allows_custom_api_host(bot):
    with pytest.raises(ValueError):
        bot._validate_outbound_url("https://api.example.com/users")

    bot._register_allowed_host("https://api.example.com")
    bot._validate_outbound_url("https://api.example.com/users")


def test_http_post_retries_on_network_error_and_succeeds(bot, monkeypatch):
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"ok": true}'
    cm = MagicMock()
    cm.__enter__.return_value = response

    safe_open = MagicMock(side_effect=[urllib.error.URLError("net"), cm])
    monkeypatch.setattr(bot, "_safe_open", safe_open)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)

    status, data = bot._http_post("https://api.telegram.org/bot/x/sendMessage", {"text": "hi"})
    assert status == 200
    assert data == {"ok": True}
    assert safe_open.call_count == 2


def test_http_post_does_not_retry_on_400(bot, monkeypatch):
    err = urllib.error.HTTPError(
        url="https://api.telegram.org/bot/x/sendMessage",
        code=400,
        msg="bad request",
        hdrs=None,
        fp=io.BytesIO(b"bad"),
    )
    safe_open = MagicMock(side_effect=err)
    monkeypatch.setattr(bot, "_safe_open", safe_open)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)

    status, data = bot._http_post("https://api.telegram.org/bot/x/sendMessage", {"text": "hi"})
    assert status == 400
    assert data is None
    assert safe_open.call_count == 1


def test_http_put_retries_retryable_status(bot, monkeypatch):
    err_503 = urllib.error.HTTPError(
        url="https://api.example.com/users/1/profile",
        code=503,
        msg="unavailable",
        hdrs=None,
        fp=io.BytesIO(b"unavailable"),
    )
    response = MagicMock()
    response.status = 204
    cm = MagicMock()
    cm.__enter__.return_value = response

    safe_open = MagicMock(side_effect=[err_503, cm])
    monkeypatch.setattr(bot, "_safe_open", safe_open)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)
    bot._register_allowed_host("https://api.example.com")

    status = bot._http_put("https://api.example.com/users/1/profile", {"profile_text": "x"})
    assert status == 204
    assert safe_open.call_count == 2


def test_http_get_returns_none_on_error(bot, monkeypatch):
    monkeypatch.setattr(bot, "_safe_open", MagicMock(side_effect=urllib.error.URLError("net")))
    assert bot._http_get("https://api.telegram.org/bot/x/getUpdates", {"offset": 1}) is None


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


def test_main_exits_on_invalid_env(bot, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "short")
    monkeypatch.setenv("API_AUTH_TOKEN", "short")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "short")
    with pytest.raises(SystemExit):
        bot.main()


def test_main_starts_polling_when_env_valid(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "https://api.example.com")

    called = {}

    def _run_polling(token, api_url, api_auth_token, api_user_hmac_secret):
        called["args"] = (token, api_url, api_auth_token, api_user_hmac_secret)

    monkeypatch.setattr(bot, "run_polling", _run_polling)
    bot.main()

    assert called["args"][0] == "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    assert called["args"][1] == "https://api.example.com"


def test_run_polling_handles_start(bot, monkeypatch):
    updates = [
        (
            [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 200},
                        "text": "/start",
                    },
                }
            ],
            2,
        ),
        KeyboardInterrupt(),
    ]

    def _get_updates(*args, **kwargs):
        item = updates.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    post_users = MagicMock(return_value=1)
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "post_users", post_users)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    post_users.assert_called_once()
    send_message.assert_called_once()


def test_run_polling_handles_profile(bot, monkeypatch):
    updates = [
        (
            [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 200},
                        "text": "/profile python backend",
                    },
                }
            ],
            2,
        ),
        KeyboardInterrupt(),
    ]

    def _get_updates(*args, **kwargs):
        item = updates.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(bot, "put_user_profile", MagicMock(return_value=True))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    send_message.assert_called_once()


def test_run_polling_backs_off_after_failed_get_updates(bot, monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    def _get_updates(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return [], None, False
        raise KeyboardInterrupt()

    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "_poll_retry_delay_sec", lambda _: 0.25)
    monkeypatch.setattr(bot.time, "sleep", lambda d: sleeps.append(d))

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert sleeps == [0.25]


def test_safe_open_uses_http_only_opener(bot, monkeypatch):
    called = {}

    def fake_open(url_or_request, timeout):
        called["args"] = (url_or_request, timeout)
        return "ok"

    monkeypatch.setattr(bot._HTTP_ONLY_OPENER, "open", fake_open)
    req = object()
    assert bot._safe_open(req, timeout=7) == "ok"
    assert called["args"] == (req, 7)


def test_shannon_entropy_empty_secret(bot):
    assert bot._shannon_entropy_bits("") == 0.0


def test_validate_secret_rejects_empty_value(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "", 32)


def test_validate_secret_rejects_low_entropy(bot):
    with pytest.raises(ValueError):
        bot._validate_secret("API_AUTH_TOKEN", "A" * 40, 32)


def test_validate_api_url_for_production_requires_host(bot):
    with pytest.raises(ValueError):
        bot._validate_api_url_for_production("https:///path")


def test_validate_outbound_url_rejects_invalid_scheme(bot):
    with pytest.raises(ValueError):
        bot._validate_outbound_url("ftp://api.telegram.org/x")


def test_validate_outbound_url_rejects_missing_host(bot):
    with pytest.raises(ValueError):
        bot._validate_outbound_url("https:///path")


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


def test_http_post_retries_on_retryable_http_status(bot, monkeypatch):
    err_503 = urllib.error.HTTPError(
        url="https://api.telegram.org/bot/x/sendMessage",
        code=503,
        msg="unavailable",
        hdrs=None,
        fp=io.BytesIO(b"busy"),
    )
    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{"ok": true}'
    cm = MagicMock()
    cm.__enter__.return_value = response

    safe_open = MagicMock(side_effect=[err_503, cm])
    monkeypatch.setattr(bot, "_safe_open", safe_open)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)

    status, data = bot._http_post("https://api.telegram.org/bot/x/sendMessage", {"text": "ok"})
    assert status == 200
    assert data == {"ok": True}
    assert safe_open.call_count == 2


def test_http_post_applies_custom_headers(bot, monkeypatch):
    captured = {}
    response = MagicMock()
    response.status = 200
    response.read.return_value = b"{}"
    cm = MagicMock()
    cm.__enter__.return_value = response

    def _safe_open(req, timeout):
        captured["headers"] = dict(req.header_items())
        return cm

    monkeypatch.setattr(bot, "_safe_open", _safe_open)

    status, data = bot._http_post(
        "https://api.telegram.org/bot/x/sendMessage",
        {"text": "ok"},
        headers={"X-Test": "yes"},
    )
    assert status == 200
    assert data == {}
    assert any(k.lower() == "x-test" and v == "yes" for k, v in captured["headers"].items())


def test_http_post_returns_500_and_none(bot, monkeypatch):
    err_500 = urllib.error.HTTPError(
        url="https://api.telegram.org/bot/x/sendMessage",
        code=500,
        msg="internal",
        hdrs=None,
        fp=io.BytesIO(b"internal error body"),
    )
    monkeypatch.setattr(bot, "_safe_open", MagicMock(side_effect=err_500))
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)

    status, data = bot._http_post("https://api.telegram.org/bot/x/sendMessage", {"text": "x"})
    assert status == 500
    assert data is None


def test_http_post_returns_zero_after_network_errors(bot, monkeypatch):
    safe_open = MagicMock(side_effect=urllib.error.URLError("down"))
    monkeypatch.setattr(bot, "_safe_open", safe_open)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)

    status, data = bot._http_post("https://api.telegram.org/bot/x/sendMessage", {"text": "x"})
    assert status == 0
    assert data is None
    assert safe_open.call_count == bot.API_RETRY_ATTEMPTS


def test_http_put_returns_non_retryable_http_status(bot, monkeypatch):
    err_400 = urllib.error.HTTPError(
        url="https://api.example.com/users/1/profile",
        code=400,
        msg="bad request",
        hdrs=None,
        fp=io.BytesIO(b"bad"),
    )
    monkeypatch.setattr(bot, "_safe_open", MagicMock(side_effect=err_400))
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)
    bot._register_allowed_host("https://api.example.com")

    status = bot._http_put("https://api.example.com/users/1/profile", {"profile_text": "x"})
    assert status == 400


def test_http_put_returns_zero_after_network_errors(bot, monkeypatch):
    safe_open = MagicMock(side_effect=urllib.error.URLError("down"))
    monkeypatch.setattr(bot, "_safe_open", safe_open)
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)
    bot._register_allowed_host("https://api.example.com")

    status = bot._http_put("https://api.example.com/users/1/profile", {"profile_text": "x"})
    assert status == 0
    assert safe_open.call_count == bot.API_RETRY_ATTEMPTS


def test_http_get_success_path(bot, monkeypatch):
    response = MagicMock()
    response.read.return_value = json.dumps({"ok": True, "result": []}).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = response
    monkeypatch.setattr(bot, "_safe_open", MagicMock(return_value=cm))

    data = bot._http_get("https://api.telegram.org/bot/x/getUpdates", {"offset": 1})
    assert data == {"ok": True, "result": []}


def test_send_message_returns_false_on_error_status(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_post", lambda *args, **kwargs: (500, None))
    assert not bot.send_message("token", 123, "hello")


def test_run_polling_skips_non_message_and_missing_ids(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1},
                {"update_id": 2, "message": {"chat": {}, "from": {}, "text": "/start"}},
            ],
            3,
        ),
        KeyboardInterrupt(),
    ]

    def _get_updates(*args, **kwargs):
        item = updates.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    post_users = MagicMock(return_value=10)
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "post_users", post_users)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    post_users.assert_not_called()
    send_message.assert_not_called()


def test_run_polling_start_registration_error(bot, monkeypatch):
    updates = [
        (
            [{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/start"}}],
            2,
        ),
        KeyboardInterrupt(),
    ]

    def _get_updates(*args, **kwargs):
        item = updates.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=None))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Ошибка регистрации" in send_message.call_args.args[2]


def test_run_polling_profile_without_text(bot, monkeypatch):
    updates = [
        (
            [{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile   "}}],
            2,
        ),
        KeyboardInterrupt(),
    ]

    def _get_updates(*args, **kwargs):
        item = updates.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Напишите: /profile" in send_message.call_args.args[2]


def test_run_polling_profile_requires_start(bot, monkeypatch):
    updates = [
        (
            [{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile python"}}],
            2,
        ),
        KeyboardInterrupt(),
    ]

    def _get_updates(*args, **kwargs):
        item = updates.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=None))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Сначала отправьте /start" in send_message.call_args.args[2]


def test_run_polling_profile_update_error(bot, monkeypatch):
    updates = [
        (
            [{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile python"}}],
            2,
        ),
        KeyboardInterrupt(),
    ]

    def _get_updates(*args, **kwargs):
        item = updates.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=1))
    monkeypatch.setattr(bot, "put_user_profile", MagicMock(return_value=False))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Ошибка обновления профиля." in send_message.call_args.args[2]


def test_main_rejects_http_api_url_in_production(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "http://api.example.com")
    with pytest.raises(SystemExit):
        bot.main()
