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
    send_keyboard = MagicMock(return_value=42)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", post_users)
    monkeypatch.setattr(bot, "send_keyboard", send_keyboard)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    post_users.assert_called_once()
    send_keyboard.assert_called_once()  # onboarding wizard starts


def test_run_polling_start_for_returning_user_shows_onboarding_actions(bot, monkeypatch):
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

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=1))
    monkeypatch.setattr(bot, "_mark_first_seen", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "_get_first_seen_ts", lambda *_args, **_kwargs: int(bot.time.time()))
    send_keyboard = MagicMock(return_value=42)
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_keyboard", send_keyboard)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    send_message.assert_not_called()
    send_keyboard.assert_called_once()
    keyboard = send_keyboard.call_args.args[3]
    callback_data = [button["callback_data"] for row in keyboard for button in row]
    assert callback_data == ["ob:restart", "ob:manual", "ob:keep"]


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
    monkeypatch.setattr(bot, "put_user_profile_status", MagicMock(return_value=204))
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
    put_user_profile_status = MagicMock(return_value=204)
    send_message = MagicMock(return_value=True)

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", post_users)
    monkeypatch.setattr(bot, "put_user_profile_status", put_user_profile_status)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert post_users.call_count == 1
    assert put_user_profile_status.call_count == 2


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
    monkeypatch.setattr(bot, "send_keyboard", MagicMock(return_value=42))

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

    assert "Отправьте следующим сообщением текст профиля" in send_message.call_args.args[2]


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

    assert "Не удалось подготовить обновление профиля" in send_message.call_args.args[2]


def test_run_polling_profile_update_error(bot, monkeypatch):
    updates = [
        (
            [{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile python"}}],
            2,
        )
    ]

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=1))
    monkeypatch.setattr(bot, "put_user_profile_status", MagicMock(return_value=500))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Сервис профилей временно недоступен" in send_message.call_args.args[2]


def test_run_polling_profile_empty_followup_text(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile"}},
                {"update_id": 2, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "   "}},
            ],
            3,
            True,
        )
    ]

    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert send_message.call_count == 2
    assert "Отправьте следующим сообщением текст профиля" in send_message.call_args_list[0].args[2]
    assert "Пустой профиль не сохраню" in send_message.call_args_list[1].args[2]


def test_run_polling_profile_invalid_message(bot, monkeypatch):
    updates = [
        (
            [{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile smoke profile"}}],
            2,
        )
    ]

    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=1))
    monkeypatch.setattr(bot, "put_user_profile_status", MagicMock(return_value=400))
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert "Профиль слишком короткий или похож на тестовую заглушку." in send_message.call_args.args[2]


def test_run_polling_profile_two_step_state_flow(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/profile"}},
                {"update_id": 2, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "python backend"}},
            ],
            3,
            True,
        )
    ]

    send_message = MagicMock(return_value=True)
    put_user_profile_status = MagicMock(return_value=204)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(bot, "put_user_profile_status", put_user_profile_status)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    put_user_profile_status.assert_called_once()
    assert send_message.call_count == 2
    assert "Отправьте следующим сообщением текст профиля" in send_message.call_args_list[0].args[2]
    assert send_message.call_args_list[1].args[2] == "Профиль обновлён."


def test_run_polling_notify_hour_two_step_state_flow(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/notify_hour"}},
                {"update_id": 2, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "9"}},
            ],
            3,
            True,
        )
    ]

    send_message = MagicMock(return_value=True)
    put_user_notify_hour = MagicMock(return_value=204)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(bot, "put_user_notify_hour", put_user_notify_hour)
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    put_user_notify_hour.assert_called_once_with("https://api.example.com", 123, 200, 9, "tok", "hmac")
    assert send_message.call_count == 2
    assert "Отправьте следующим сообщением час" in send_message.call_args_list[0].args[2]
    assert "09:00 МСК" in send_message.call_args_list[1].args[2]


def test_run_polling_notify_hour_prompt_blocked_for_non_pro(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/notify_hour"}},
            ],
            2,
            True,
        )
    ]

    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(bot, "get_user_is_pro", MagicMock(return_value=False))
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert send_message.call_count == 1
    assert "только Pro-пользователям" in send_message.call_args.args[2]
    assert bot._get_conversation_state(200) is None


def test_run_polling_notify_hour_prompt_allowed_when_pro_flag_unknown(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/notify_hour"}},
            ],
            2,
            True,
        )
    ]

    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(bot, "get_user_is_pro", MagicMock(return_value=None))
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert send_message.call_count == 1
    assert "Отправьте следующим сообщением час" in send_message.call_args.args[2]
    assert bot._get_conversation_state(200) == "await_notify_hour"


def test_handle_notify_hour_submission_clears_state_on_forbidden(bot, monkeypatch):
    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(bot, "put_user_notify_hour", MagicMock(return_value=403))
    monkeypatch.setattr(bot, "send_message", send_message)

    bot._set_conversation_state(200, "await_notify_hour")
    handled = bot._handle_notify_hour_submission(
        "token",
        100,
        200,
        "9",
        "https://api.example.com",
        "tok",
        "hmac",
    )

    assert handled is True
    assert bot._get_conversation_state(200) is None
    assert "только Pro-пользователям" in send_message.call_args.args[2]


def test_run_polling_handles_stats_command(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/stats"}},
            ],
            2,
            True,
        )
    ]

    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=123))
    monkeypatch.setattr(
        bot,
        "get_user_stats",
        MagicMock(
            return_value={
                "period_days": 7,
                "projects_found": 42,
                "projects_shown": 12,
                "projects_filtered_other": 30,
                "projects_filtered_by_budget": 18,
                "budget_filter_active": True,
            },
        ),
    )
    send_with_reply_keyboard = MagicMock(return_value=None)
    monkeypatch.setattr(bot, "send_message", send_message)
    monkeypatch.setattr(bot, "send_with_reply_keyboard", send_with_reply_keyboard)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert send_with_reply_keyboard.call_count == 1
    text = send_with_reply_keyboard.call_args.args[2]
    assert "Статистика за последние 7 дней" in text
    assert "Найдено подходящих проектов: 42" in text
    assert "Показано вам: 12" in text
    assert "Отфильтровано по бюджету: 18" in text


def test_run_polling_stats_requires_start(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/stats"}},
            ],
            2,
            True,
        )
    ]

    send_message = MagicMock(return_value=True)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=None))
    monkeypatch.setattr(bot, "send_message", send_message)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert send_message.call_count == 1
    assert "Сначала отправьте /start" in send_message.call_args.args[2]


def test_run_polling_skips_duplicate_update_id(bot, monkeypatch):
    updates = [
        (
            [
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/start"}},
                {"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/start"}},
            ],
            2,
            True,
        )
    ]

    post_users = MagicMock(return_value=1)
    send_keyboard = MagicMock(return_value=42)
    monkeypatch.setattr(bot, "get_updates", _updates_then_interrupt(updates))
    monkeypatch.setattr(bot, "post_users", post_users)
    monkeypatch.setattr(bot, "send_keyboard", send_keyboard)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    post_users.assert_called_once()
    send_keyboard.assert_called_once()  # onboarding wizard, no duplicate


def test_run_polling_preserves_offset_after_failed_update_and_retries(bot, monkeypatch):
    offsets: list[int | None] = []
    calls = {"n": 0}

    def _get_updates(token, offset, timeout=30, *, include_status=False):
        _ = token
        _ = timeout
        offsets.append(offset)
        calls["n"] += 1
        if calls["n"] in (1, 2):
            result = ([{"update_id": 1, "message": {"chat": {"id": 100}, "from": {"id": 200}, "text": "/start"}}], 2)
            if include_status:
                return result[0], result[1], True
            return result
        raise KeyboardInterrupt()

    failures = {"n": 0}

    def _handle_update(*args, **kwargs):
        failures["n"] += 1
        if failures["n"] == 1:
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr(bot, "get_updates", _get_updates)
    monkeypatch.setattr(bot, "_handle_update", _handle_update)

    with pytest.raises(KeyboardInterrupt):
        bot.run_polling("token", "https://api.example.com", "tok", "hmac")

    assert offsets[:2] == [None, None]
