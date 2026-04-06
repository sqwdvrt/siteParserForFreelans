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
    content_length: int | None = None,
):
    handler = object.__new__(bot._WebhookHandler)
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    handler.path = path
    handler.headers = {
        "Content-Length": str(len(body) if content_length is None else content_length),
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


class _GuardedBody:
    def read(self, _size: int = -1):
        raise AssertionError("webhook handler must not read oversized request bodies")


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


def test_webhook_rejects_oversized_payload_before_reading_body(bot):
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload=b"{}",
        secret="a" * 32,
        content_length=bot.WEBHOOK_MAX_BODY_BYTES + 1,
    )
    handler.rfile = _GuardedBody()

    handler.do_POST()

    assert statuses == [413]


def test_webhook_returns_200_after_durable_enqueue_without_inline_processing(bot, monkeypatch):
    enqueue_calls: list[dict] = []
    inline_handled = threading.Event()

    def _enqueue_webhook_update(update, token, api_url, api_auth_token, api_user_hmac_secret):
        enqueue_calls.append(
            {
                "update": update,
                "token": token,
                "api_url": api_url,
                "api_auth_token": api_auth_token,
                "api_user_hmac_secret": api_user_hmac_secret,
            }
        )
        return True

    def _handle_update(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = update
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        inline_handled.set()
        return True

    monkeypatch.setattr(bot, "_enqueue_webhook_update", _enqueue_webhook_update, raising=False)
    monkeypatch.setattr(bot, "_handle_update", _handle_update)
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload={"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}},
        secret="a" * 32,
    )

    handler.do_POST()

    assert statuses == [200]
    assert inline_handled.is_set() is False
    assert enqueue_calls == [
        {
            "update": {"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}},
            "token": "bot-token",
            "api_url": "https://api.example.com",
            "api_auth_token": "api-token",
            "api_user_hmac_secret": "h" * 32,
        }
    ]


def test_webhook_handles_message_update(bot, monkeypatch):
    enqueued: list[dict] = []

    def _enqueue(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        enqueued.append(update)
        return True

    monkeypatch.setattr(bot, "_enqueue_webhook_update", _enqueue)
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload={"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}},
        secret="a" * 32,
    )

    handler.do_POST()

    assert statuses == [200]
    assert enqueued[0]["update_id"] == 1


def test_webhook_waits_for_durable_enqueue_before_sending_200(bot, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _enqueue(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = update
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        started.set()
        assert release.wait(timeout=1)
        finished.set()
        return True

    monkeypatch.setattr(bot, "_enqueue_webhook_update", _enqueue)
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload={"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}},
        secret="a" * 32,
    )

    request_thread = threading.Thread(target=handler.do_POST)
    request_thread.start()

    assert started.wait(timeout=1)
    assert statuses == []
    assert finished.is_set() is False
    release.set()
    assert finished.wait(timeout=1)
    request_thread.join(timeout=1)
    assert request_thread.is_alive() is False
    assert statuses == [200]


def test_webhook_returns_500_when_processing_fails(bot, monkeypatch):
    def _enqueue(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = update
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        raise RuntimeError("boom")

    monkeypatch.setattr(bot, "_enqueue_webhook_update", _enqueue)
    handler, statuses = _make_handler(
        bot,
        path="/webhook",
        payload={"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}},
        secret="a" * 32,
    )

    handler.do_POST()

    assert statuses == [500]


def test_webhook_returns_200_for_duplicate_retry_after_durable_enqueue(bot, monkeypatch):
    enqueue_results = iter([True, True])
    enqueued: list[int] = []

    def _enqueue(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        enqueued.append(update["update_id"])
        return next(enqueue_results)

    monkeypatch.setattr(bot, "_enqueue_webhook_update", _enqueue)
    payload = {"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/start"}}
    first_handler, first_statuses = _make_handler(
        bot,
        path="/webhook",
        payload=payload,
        secret="a" * 32,
    )
    second_handler, second_statuses = _make_handler(
        bot,
        path="/webhook",
        payload=payload,
        secret="a" * 32,
    )
    first_handler.do_POST()
    second_handler.do_POST()
    assert first_statuses == [200]
    assert second_statuses == [200]
    assert enqueued == [1, 1]


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


def test_run_webhook_registers_and_keeps_webhook_on_sigterm(bot, monkeypatch):
    handlers: dict[int, object] = {}
    shutdown_called = threading.Event()
    serving_started = threading.Event()
    set_calls: list[tuple[str, str, str, int]] = []

    class FakeServer:
        def __init__(self, server_address, handler_cls) -> None:
            self.server_address = server_address
            self.handler_cls = handler_cls

        def serve_forever(self) -> None:
            serving_started.set()
            handlers[bot.signal.SIGTERM](bot.signal.SIGTERM, None)
            assert shutdown_called.wait(timeout=1)

        def shutdown(self) -> None:
            shutdown_called.set()

        def server_close(self) -> None:
            return None

    monkeypatch.setattr(
        bot,
        "set_webhook",
        lambda token, url, secret_token, max_connections=40: (
            serving_started.is_set()
            and set_calls.append((token, url, secret_token, max_connections)) is None
            and True
        ),
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
    assert serving_started.is_set()
    assert shutdown_called.is_set()


def test_run_webhook_updates_heartbeat_while_idle(bot, monkeypatch):
    handlers: dict[int, object] = {}
    heartbeat_touched = threading.Event()

    class FakeServer:
        def __init__(self, server_address, handler_cls) -> None:
            self.server_address = server_address
            self.handler_cls = handler_cls

        def serve_forever(self) -> None:
            assert heartbeat_touched.wait(timeout=1)
            handlers[bot.signal.SIGTERM](bot.signal.SIGTERM, None)

        def shutdown(self) -> None:
            return None

        def server_close(self) -> None:
            return None

    monkeypatch.setenv("TELEGRAM_HEARTBEAT_FILE", "/tmp/test-telegram-heartbeat")
    monkeypatch.setenv("TELEGRAM_HEARTBEAT_MAX_AGE_SEC", "3")
    monkeypatch.setattr(bot, "set_webhook", lambda *args, **kwargs: True)
    monkeypatch.setattr(bot.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))
    monkeypatch.setattr(bot, "_touch_heartbeat", lambda _path: heartbeat_touched.set())

    bot.run_webhook(
        "bot-token",
        "https://bot.example.com/webhook",
        "0123456789abcdef0123456789abcdef",
        "https://api.example.com",
        "api-token",
        "0123456789abcdef0123456789abcdef",
        port=8080,
        max_connections=40,
        server_factory=FakeServer,
    )

    assert heartbeat_touched.is_set()


def test_set_webhook_rejects_non_ok_response(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_post", lambda *args, **kwargs: (200, {"ok": False, "description": "bad webhook"}))

    assert bot.set_webhook("bot-token", "https://bot.example.com/webhook", "a" * 32) is False


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
