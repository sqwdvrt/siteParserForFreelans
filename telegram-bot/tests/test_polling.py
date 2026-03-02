from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _updates_then_interrupt(items: list[object]):
    sequence = list(items) + [KeyboardInterrupt()]

    def _get_updates(*args, **kwargs):
        item = sequence.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return _get_updates


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
        )
    ]

    post_users = MagicMock(return_value=1)
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
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
        )
    ]

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(bot, "put_user_profile", MagicMock(return_value=True))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    send_message.assert_called_once()


def test_run_polling_profile_uses_cached_user_id(bot, monkeypatch):
    updates = [
        (
            [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 200},
                        "text": "/profile python",
                    },
                },
                {
                    "update_id": 2,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 200},
                        "text": "/profile golang",
                    },
                },
            ],
            3,
        )
    ]

    post_users = MagicMock(return_value=123)
    put_user_profile = MagicMock(return_value=True)
    send_message = MagicMock(return_value=True)

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", post_users)
    monkeypatch.setattr(bot, "put_user_profile", put_user_profile)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert post_users.call_count == 1
    assert put_user_profile.call_count == 2


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


def test_run_polling_touches_heartbeat_file(bot, monkeypatch):
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
        )
    ]

    heartbeat_calls: list[str] = []
    monkeypatch.setenv("TELEGRAM_HEARTBEAT_FILE", "/tmp/tg-heartbeat-test")
    monkeypatch.setattr(bot, "_touch_heartbeat", lambda path: heartbeat_calls.append(path))
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=1))
    monkeypatch.setattr(bot, "send_message", MagicMock(return_value=True))

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert heartbeat_calls
    assert all(path == "/tmp/tg-heartbeat-test" for path in heartbeat_calls)


def test_run_polling_skips_non_message_and_missing_ids(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1},
                {"update_id": 2, "message": {"chat": {}, "from": {}, "text": "/start"}},
            ],
            3,
        )
    ]

    post_users = MagicMock(return_value=10)
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
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
        )
    ]

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
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
        )
    ]

    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Напишите: /profile" in send_message.call_args.args[2]


def test_run_polling_profile_requires_start(bot, monkeypatch):
    updates = [
        (
            [{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile python"}}],
            2,
        )
    ]

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
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
        )
    ]

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=1))
    monkeypatch.setattr(bot, "put_user_profile", MagicMock(return_value=False))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Ошибка обновления профиля." in send_message.call_args.args[2]
