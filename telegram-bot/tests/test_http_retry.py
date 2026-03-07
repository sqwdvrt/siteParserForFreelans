from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_should_retry_status_for_retryable_codes(bot, status):
    assert bot._should_retry_status(status) is True


@pytest.mark.parametrize("status", [200, 201, 204, 400, 401, 403, 404])
def test_should_retry_status_for_non_retryable_codes(bot, status):
    assert bot._should_retry_status(status) is False


def test_retry_delay_sec_within_limits(bot, monkeypatch):
    monkeypatch.setattr(bot.random, "uniform", lambda a, b: 0.2)
    delay = bot._retry_delay_sec(100)
    assert delay == pytest.approx(bot.API_RETRY_MAX_DELAY_SEC + 0.2)


def test_poll_retry_delay_sec_within_limits(bot, monkeypatch):
    monkeypatch.setattr(bot.random, "uniform", lambda a, b: 0.2)
    delay = bot._poll_retry_delay_sec(100)
    assert delay == pytest.approx(bot.POLL_RETRY_MAX_DELAY_SEC + 0.2)


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


def test_http_post_applies_custom_headers(bot, monkeypatch):
    captured: dict[str, dict[str, str]] = {}
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


def test_http_get_returns_none_on_error(bot, monkeypatch):
    monkeypatch.setattr(bot, "_safe_open", MagicMock(side_effect=urllib.error.URLError("net")))
    assert bot._http_get("https://api.telegram.org/bot/x/getUpdates", {"offset": 1}) is None


def test_http_get_logs_status_and_safe_description_for_http_error(bot, monkeypatch, caplog):
    err = urllib.error.HTTPError(
        url="https://api.telegram.org/bot/secret-token/getUpdates",
        code=409,
        msg="conflict",
        hdrs=None,
        fp=io.BytesIO(
            json.dumps(
                {
                    "ok": False,
                    "error_code": 409,
                    "description": "Conflict: terminated by other getUpdates request",
                }
            ).encode("utf-8")
        ),
    )
    monkeypatch.setattr(bot, "_safe_open", MagicMock(side_effect=err))

    with caplog.at_level("WARNING"):
        assert bot._http_get("https://api.telegram.org/bot/secret-token/getUpdates", {"offset": 1}) is None

    assert "status=409" in caplog.text
    assert "terminated by other getUpdates request" in caplog.text
    assert "secret-token" not in caplog.text


def test_http_get_success_path(bot, monkeypatch):
    response = MagicMock()
    response.read.return_value = json.dumps({"ok": True, "result": []}).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = response
    monkeypatch.setattr(bot, "_safe_open", MagicMock(return_value=cm))

    data = bot._http_get("https://api.telegram.org/bot/x/getUpdates", {"offset": 1})
    assert data == {"ok": True, "result": []}
