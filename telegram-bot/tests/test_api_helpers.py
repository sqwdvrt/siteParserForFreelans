from __future__ import annotations

import copy
import urllib.error
import urllib.parse
import urllib.request

import pytest


class _FakeBatchStateStore:
    def __init__(self, sessions: dict[str, dict] | None = None):
        self.sessions = copy.deepcopy(sessions or {})

    def get_batch_session(self, session_id: str):
        session = self.sessions.get(session_id)
        return copy.deepcopy(session) if session is not None else None

    def set_batch_session(self, session_id: str, session: dict) -> None:
        self.sessions[session_id] = copy.deepcopy(session)

    def clear_batch_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)


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


def test_put_user_preferences_status_returns_status_code(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_put", lambda *args, **kwargs: 204)
    status = bot.put_user_preferences_status(
        "https://api.example.com",
        1,
        123,
        {"preferred_sources": ["kwork"]},
        "tok",
        "hmac",
    )
    assert status == 204


def test_put_user_pause_status_returns_status_code(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_put", lambda *args, **kwargs: 204)
    status = bot.put_user_pause_status(
        "https://api.example.com",
        1,
        123,
        None,
        "tok",
        "hmac",
    )
    assert status == 204


def test_post_pro_upgrade_intent_returns_status_code(bot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        bot,
        "_http_post",
        lambda url, payload, **kwargs: calls.append((url, payload, kwargs)) or (204, None),
    )

    status = bot.post_pro_upgrade_intent(
        "https://api.example.com",
        7,
        123,
        "telegram_pro_screen",
        "tok",
        "hmac",
    )

    assert status == 204
    assert len(calls) == 1
    assert calls[0][0] == "https://api.example.com/users/7/pro-upgrade-intent"
    assert calls[0][1] == {"source": "telegram_pro_screen"}
    assert "Authorization" in calls[0][2]["headers"]


def test_debug_match_helper_formats_payload(bot):
    from handlers.debug import format_debug_match_message

    text = format_debug_match_message(
        "https://kwork.ru/projects/1",
        {
            "project": {"title": "FastAPI backend", "source": "kwork"},
            "embedding_similarity": 0.41,
            "similarity_threshold": 0.62,
            "rerank_score": 0.58,
            "rerank_threshold": 0.60,
            "preference_filter": {"passed": True, "reason": ""},
            "final_score": 0.34,
            "job_stack": ["fastapi", "postgresql"],
            "profile_stack": ["django", "postgresql"],
            "stack_intersection": ["postgresql"],
            "conclusion": "не прошёл бы ANN порог (0.41 < 0.62)",
        },
    )

    assert "Диагностика матчинга" in text
    assert "Embedding similarity: 0.41" in text
    assert "Rerank score: 0.58" in text
    assert "Final score: 0.34" in text
    assert "Пересечение: postgresql" in text


def test_send_debug_match_requires_admin(bot, monkeypatch):
    messages = []
    monkeypatch.delenv("ADMIN_TELEGRAM_ID", raising=False)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: messages.append(args[2]) or True)

    bot._send_debug_match_diagnostics(
        "token",
        123,
        999,
        "https://kwork.ru/projects/1",
        "https://api.example.com",
        "api-token",
        "h" * 32,
    )

    assert messages == ["Эта команда доступна только администратору."]


def test_set_my_commands_registers_public_command_surface(bot, monkeypatch):
    captured = {}

    def _capture(url, payload, *args, **kwargs):
        captured["url"] = url
        captured["payload"] = payload
        return 200, {"ok": True}

    monkeypatch.setattr(bot, "_http_post", _capture)

    assert bot.set_my_commands("token") is True

    commands = captured["payload"]["commands"]
    command_names = [item["command"] for item in commands]
    assert command_names == ["start", "profile", "filters", "status", "help", "pro"]
    assert "notify_hour" not in command_names


def test_filters_source_toggle_updates_preferences_and_rerenders(bot, monkeypatch):
    edits = []
    monkeypatch.setattr(
        bot,
        "get_user_preferences",
        lambda *args, **kwargs: {
            "preferred_sources": ["kwork"],
            "min_budget": 5000,
            "include_keywords": [],
            "exclude_keywords": [],
        },
    )
    put_calls = []
    monkeypatch.setattr(
        bot,
        "put_user_preferences_status",
        lambda *args, **kwargs: put_calls.append(args[3]) or 204,
    )
    monkeypatch.setattr(bot, "edit_message_text", lambda *args, **kwargs: edits.append((args, kwargs)))
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *args, **kwargs: 42)
    monkeypatch.setattr(bot, "answer_callback_query", lambda *args, **kwargs: None)

    callback = {
        "id": "cb-filter-src",
        "data": "flt:src:toggle:flru",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 99},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "h" * 32)

    assert put_calls
    assert put_calls[0]["preferred_sources"] == ["kwork", "flru"]
    assert edits
    assert "Источники" in edits[0][0][3]


def test_filters_reset_clears_preferences_and_rerenders(bot, monkeypatch):
    edits = []
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *args, **kwargs: 42)
    monkeypatch.setattr(bot, "answer_callback_query", lambda *args, **kwargs: None)
    put_calls = []
    monkeypatch.setattr(
        bot,
        "put_user_preferences_status",
        lambda *args, **kwargs: put_calls.append(args[3]) or 204,
    )
    monkeypatch.setattr(
        bot,
        "get_user_preferences",
        lambda *args, **kwargs: {
            "preferred_sources": [],
            "include_keywords": [],
            "exclude_keywords": [],
        },
    )
    monkeypatch.setattr(bot, "edit_message_text", lambda *args, **kwargs: edits.append((args, kwargs)))

    callback = {
        "id": "cb-filter-reset",
        "data": "flt:reset",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 99},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "h" * 32)

    assert put_calls
    assert put_calls[0] == {
        "preferred_sources": [],
        "include_keywords": [],
        "exclude_keywords": [],
        "min_budget": None,
        "max_budget": None,
    }
    assert edits


def test_profile_edit_callback_sets_state_and_prompts(bot, monkeypatch):
    messages = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: messages.append((args, kwargs)) or True)

    callback = {
        "id": "cb-profile-edit",
        "data": "prf:edit",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 77},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "h" * 32)

    assert bot._get_conversation_state(987654) == "await_profile"
    assert messages
    assert "Отправьте следующим сообщением текст профиля" in messages[0][0][2]


def test_profile_tips_callback_rerenders_message(bot, monkeypatch):
    edits = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "edit_message_text", lambda *args, **kwargs: edits.append((args, kwargs)))

    callback = {
        "id": "cb-profile-tips",
        "data": "prf:tips",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 77},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "h" * 32)

    assert edits
    assert "Как улучшить профиль" in edits[0][0][3]


def test_pause_callback_updates_pause_and_rerenders_message(bot, monkeypatch):
    edits = []
    put_calls = []
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *args, **kwargs: 42)
    monkeypatch.setattr(bot, "answer_callback_query", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "put_user_pause_status",
        lambda *args, **kwargs: put_calls.append(args[3]) or 204,
    )
    monkeypatch.setattr(bot, "edit_message_text", lambda *args, **kwargs: edits.append((args, kwargs)))

    callback = {
        "id": "cb-pause-1d",
        "data": "pau:1d",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 77},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "h" * 32)

    assert len(put_calls) == 1
    assert put_calls[0] is not None
    assert edits
    assert "Пауза включена" in edits[0][0][3]


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

    def fake_http_post(url, data, headers=None, *, make_headers=None):
        captured["headers"] = make_headers() if make_headers is not None else (headers or {})
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

    def fake_http_post(url, data, headers=None, *, make_headers=None):
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


def test_batch_nav_callback_edits_message_in_place(bot, monkeypatch):
    edits = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: edits.append(a))
    monkeypatch.setattr(bot, "_STATE_STORE", _FakeBatchStateStore({
        "session-1": {
            "version": 1,
            "telegram_id": 987654,
            "current_index": 0,
            "items": [
                {
                    "job_id": 11,
                    "title": "Card one",
                    "description": "First card",
                    "budget": "1000 ₽",
                    "url": "https://example.com/job/11",
                    "why_it_fits": "Matches skills",
                    "score_percent": 81,
                    "posted_at_unix": 1700000000,
                    "created_at_unix": 1700000100,
                },
                {
                    "job_id": 12,
                    "title": "Card two",
                    "description": "Second card",
                    "budget": "2000 ₽",
                    "url": "https://example.com/job/12",
                    "why_it_fits": "Better fit",
                    "score_percent": 91,
                    "posted_at_unix": 1700000200,
                    "created_at_unix": 1700000300,
                },
            ],
        }
    }))

    callback = {
        "id": "cb-nav",
        "data": "nav:n:session-1:1",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 55},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert len(edits) == 1
    _token, chat_id, message_id, text, keyboard = edits[0]
    assert chat_id == 123
    assert message_id == 55
    assert "Card two" in text
    assert keyboard[0][0]["callback_data"] == "nav:p:session-1:0"
    assert keyboard[0][1]["callback_data"] == "nav:i:session-1:1"
    assert len(keyboard[0]) == 2
    assert bot._STATE_STORE.sessions["session-1"]["current_index"] == 1


def test_batch_nav_missing_session_prompts_existing_profile_command(bot, monkeypatch):
    answers = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: answers.append((a, kw)))
    monkeypatch.setattr(bot, "_STATE_STORE", _FakeBatchStateStore({}))

    callback = {
        "id": "cb-nav-missing",
        "data": "nav:n:missing-session:1",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 55},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert len(answers) >= 1
    _args, kwargs = answers[0]
    assert kwargs["text"] == "Подборка устарела. Обновите профиль: /profile"
    assert "/jobs" not in kwargs["text"]
    assert kwargs["show_alert"] is True


def test_batch_feedback_auto_advances_to_next_card(bot, monkeypatch):
    edits = []
    feedback_calls = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: edits.append(a))
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: 42)
    monkeypatch.setattr(bot, "post_feedback", lambda *a, **kw: feedback_calls.append(a) or True)
    monkeypatch.setattr(bot, "_STATE_STORE", _FakeBatchStateStore({
        "session-2": {
            "version": 1,
            "telegram_id": 987654,
            "current_index": 0,
            "items": [
                {
                    "job_id": 21,
                    "title": "First job",
                    "description": "Alpha",
                    "budget": "1000 ₽",
                    "url": "https://example.com/job/21",
                    "why_it_fits": "Fits well",
                    "score_percent": 70,
                    "posted_at_unix": 1700000400,
                    "created_at_unix": 1700000500,
                },
                {
                    "job_id": 22,
                    "title": "Second job",
                    "description": "Beta",
                    "budget": "2000 ₽",
                    "url": "https://example.com/job/22",
                    "why_it_fits": "Fits better",
                    "score_percent": 88,
                    "posted_at_unix": 1700000600,
                    "created_at_unix": 1700000700,
                },
            ],
        }
    }))

    callback = {
        "id": "cb-fb",
        "data": "fb:g:session-2:0:21",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 56},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert len(feedback_calls) == 1
    _api_url, called_user_id, called_telegram_id, job_id, feedback = feedback_calls[0][:5]
    assert called_user_id == 42
    assert called_telegram_id == 987654
    assert job_id == 21
    assert feedback == "good"
    assert len(edits) == 1
    assert "Second job" in edits[0][3]
    assert bot._STATE_STORE.sessions["session-2"]["current_index"] == 1


def test_batch_feedback_on_last_card_replaces_with_completion_text(bot, monkeypatch):
    edits = []
    feedback_calls = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: edits.append(a))
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: 42)
    monkeypatch.setattr(bot, "post_feedback", lambda *a, **kw: feedback_calls.append(a) or True)
    monkeypatch.setattr(bot, "_STATE_STORE", _FakeBatchStateStore({
        "session-3": {
            "version": 1,
            "telegram_id": 987654,
            "current_index": 1,
            "items": [
                {
                    "job_id": 31,
                    "title": "Prev job",
                    "description": "Alpha",
                    "budget": "1000 ₽",
                    "url": "https://example.com/job/31",
                    "why_it_fits": "Fits",
                    "score_percent": 71,
                    "posted_at_unix": 1700000800,
                    "created_at_unix": 1700000900,
                },
                {
                    "job_id": 32,
                    "title": "Last job",
                    "description": "Omega",
                    "budget": "3000 ₽",
                    "url": "https://example.com/job/32",
                    "why_it_fits": "Best fit",
                    "score_percent": 97,
                    "posted_at_unix": 1700001000,
                    "created_at_unix": 1700001100,
                },
            ],
        }
    }))

    callback = {
        "id": "cb-fb-last",
        "data": "fb:b:session-3:1:32",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 57},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert len(feedback_calls) == 1
    assert len(edits) == 1
    assert "Подборка завершена" in edits[0][3]
    assert edits[0][4] == []
    assert "session-3" not in bot._STATE_STORE.sessions


def test_batch_stale_callback_is_ignored_gracefully(bot, monkeypatch):
    edits = []
    feedback_calls = []
    answers = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: answers.append(a))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: edits.append(a))
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: 42)
    monkeypatch.setattr(bot, "post_feedback", lambda *a, **kw: feedback_calls.append(a) or True)
    monkeypatch.setattr(bot, "_STATE_STORE", _FakeBatchStateStore({
        "session-4": {
            "version": 1,
            "telegram_id": 987654,
            "current_index": 1,
            "items": [
                {
                    "job_id": 41,
                    "title": "Old job",
                    "description": "Alpha",
                    "budget": "1000 ₽",
                    "url": "https://example.com/job/41",
                    "why_it_fits": "Fits",
                    "score_percent": 60,
                    "posted_at_unix": 1700001200,
                    "created_at_unix": 1700001300,
                },
                {
                    "job_id": 42,
                    "title": "Current job",
                    "description": "Beta",
                    "budget": "2000 ₽",
                    "url": "https://example.com/job/42",
                    "why_it_fits": "Fits now",
                    "score_percent": 85,
                    "posted_at_unix": 1700001400,
                    "created_at_unix": 1700001500,
                },
            ],
        }
    }))

    callback = {
        "id": "cb-stale",
        "data": "fb:g:session-4:0:41",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 58},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert answers
    assert not edits
    assert not feedback_calls
    assert bot._STATE_STORE.sessions["session-4"]["current_index"] == 1


def test_batch_feedback_missing_session_prompts_existing_profile_command(bot, monkeypatch):
    answers = []
    monkeypatch.setattr(bot, "answer_callback_query", lambda *a, **kw: answers.append((a, kw)))
    monkeypatch.setattr(bot, "_STATE_STORE", _FakeBatchStateStore({}))

    callback = {
        "id": "cb-fb-missing",
        "data": "fb:g:missing-session:0:41",
        "from": {"id": 987654},
        "message": {"chat": {"id": 123}, "message_id": 58},
    }
    bot.handle_callback(callback, "token", "https://api.example.com", "tok", "hmac")

    assert len(answers) >= 1
    _args, kwargs = answers[0]
    assert kwargs["text"] == "Подборка устарела. Обновите профиль: /profile"
    assert "/jobs" not in kwargs["text"]
    assert kwargs["show_alert"] is True


def test_render_metrics_contains_counters_and_ready_gauge(bot):
    bot._METRICS.inc("telegram_bot_updates_total", transport="polling", result="handled")
    bot._METRICS.inc("telegram_bot_commands_total", command="start", result="ok")
    bot._METRICS.set_ready(True)

    payload = bot._render_metrics()

    assert "telegram_bot_updates_total{result=\"handled\",transport=\"polling\"} 1" in payload
    assert "telegram_bot_commands_total{command=\"start\",result=\"ok\"} 1" in payload
    assert "telegram_bot_ready 1" in payload
