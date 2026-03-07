from __future__ import annotations

import io
import json
import threading
from unittest.mock import MagicMock


def _make_handler(
    bot,
    *,
    path: str,
    payload: dict | bytes,
    secret: str | None,
):
    handler = object.__new__(bot._WebhookHandler)
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    handler.path = path
    handler.headers = {
        "Content-Length": str(len(body)),
        "X-Telegram-Bot-Api-Secret-Token": "" if secret is None else secret,
    }
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.client_address = ("127.0.0.1", 12345)
    handler.secret_token = "a" * 32
    handler.bot_token = "bot-token"
    handler.api_url = "https://api.example.com"
    handler.api_auth_token = "api-token"
    handler.api_user_hmac_secret = "h" * 32
    statuses: list[int] = []
    handler.send_response = lambda code: statuses.append(code)
    handler.end_headers = lambda: None
    return handler, statuses


def test_webhook_rejects_wrong_path(bot):
    handler, statuses = _make_handler(
        bot,
        path="/other",
        payload={"update_id": 1},
        secret="a" * 32,
    )

    handler.do_POST()

    assert statuses == [404]


def test_webhook_rejects_bad_secret(bot):
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload={"update_id": 1},
        secret="b" * 32,
    )

    handler.do_POST()

    assert statuses == [403]


def test_webhook_handles_message_update(bot, monkeypatch):
    processed = threading.Event()
    handled: list[dict] = []

    def _handle_update(update, token, api_url, api_auth_token, api_user_hmac_secret):
        handled.append(
            {
                "update": update,
                "token": token,
                "api_url": api_url,
                "api_auth_token": api_auth_token,
                "api_user_hmac_secret": api_user_hmac_secret,
            }
        )
        processed.set()

    monkeypatch.setattr(bot, "_handle_update", _handle_update)
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload={"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}},
        secret="a" * 32,
    )

    handler.do_POST()

    assert statuses == [200]
    assert processed.wait(timeout=1)
    assert handled[0]["update"]["update_id"] == 1
    assert handled[0]["token"] == "bot-token"


def test_webhook_returns_200_before_processing_completes(bot, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _handle_update(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = update
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        started.set()
        assert release.wait(timeout=1)
        finished.set()

    monkeypatch.setattr(bot, "_handle_update", _handle_update)
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload={"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}},
        secret="a" * 32,
    )

    handler.do_POST()

    assert statuses == [200]
    assert started.wait(timeout=1)
    assert finished.is_set() is False
    release.set()
    assert finished.wait(timeout=1)


def test_handle_update_routes_callback_query(bot, monkeypatch):
    handle_callback = MagicMock()
    monkeypatch.setattr(bot, "handle_callback", handle_callback)

    bot._handle_update(
        {
            "update_id": 1,
            "callback_query": {"id": "cb-1", "from": {"id": 42}, "data": "fb:g:7"},
        },
        "bot-token",
        "https://api.example.com",
        "api-token",
        "h" * 32,
    )

    handle_callback.assert_called_once()


def test_run_webhook_registers_and_deletes_webhook_on_sigterm(bot, monkeypatch):
    handlers: dict[int, object] = {}
    shutdown_called = threading.Event()
    delete_calls: list[tuple[str, bool]] = []
    set_calls: list[tuple[str, str, str, int]] = []

    class FakeServer:
        def __init__(self, server_address, handler_cls) -> None:
            self.server_address = server_address
            self.handler_cls = handler_cls

        def serve_forever(self) -> None:
            handlers[bot.signal.SIGTERM](bot.signal.SIGTERM, None)
            assert shutdown_called.wait(timeout=1)

        def shutdown(self) -> None:
            shutdown_called.set()

        def server_close(self) -> None:
            return None

    monkeypatch.setattr(
        bot,
        "set_webhook",
        lambda token, url, secret_token, max_connections=40: set_calls.append(
            (token, url, secret_token, max_connections)
        ) or True,
    )
    monkeypatch.setattr(
        bot,
        "delete_webhook",
        lambda token, drop_pending=False: delete_calls.append((token, drop_pending)),
    )
    monkeypatch.setattr(bot.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))

    bot.run_webhook(
        "bot-token",
        "https://bot.example.com/webhook",
        "0123456789abcdef0123456789abcdef",
        "https://api.example.com",
        "api-token",
        "0123456789abcdef0123456789abcdef",
        port=8080,
        max_connections=55,
        server_factory=FakeServer,
    )

    assert set_calls == [("bot-token", "https://bot.example.com/webhook", "0123456789abcdef0123456789abcdef", 55)]
    assert shutdown_called.is_set()
    assert delete_calls == [("bot-token", False)]


def test_process_update_skips_duplicate_webhook_delivery(bot, monkeypatch):
    handled: list[int] = []

    def _handle_update(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        handled.append(update["update_id"])
        return True

    monkeypatch.setattr(bot, "_handle_update", _handle_update)
    update = {"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}}

    assert bot._process_update(update, "webhook", "bot-token", "https://api.example.com", "tok", "h" * 32) is True
    assert bot._process_update(update, "webhook", "bot-token", "https://api.example.com", "tok", "h" * 32) is True
    assert handled == [1]
