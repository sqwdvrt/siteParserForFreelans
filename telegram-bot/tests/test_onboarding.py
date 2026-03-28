from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_onboarding_handle_callback(
    bot,
    *,
    data: str,
    telegram_id: int = 42,
    chat_id: int = 100,
    message_id: int = 1,
    api_url: str = "https://api.example.com",
    api_auth_token: str = "tok",
    api_user_hmac_secret: str = "h" * 32,
) -> None:
    bot._onboarding_handle_callback(
        data,
        "bot-token",
        chat_id,
        message_id,
        telegram_id,
        api_url,
        api_auth_token,
        api_user_hmac_secret,
    )


def _make_update(*, text: str, telegram_id: int = 42, chat_id: int = 100, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": telegram_id},
            "text": text,
        },
    }


def _call_handle_update(
    bot,
    update: dict,
    *,
    api_url: str = "https://api.example.com",
    api_auth_token: str = "tok",
    api_user_hmac_secret: str = "h" * 32,
) -> bool:
    return bot._handle_update(
        update,
        "bot-token",
        api_url,
        api_auth_token,
        api_user_hmac_secret,
    )


# ---------------------------------------------------------------------------
# _parse_onboarding_state
# ---------------------------------------------------------------------------

def test_parse_onboarding_state_returns_none_for_empty_string(bot):
    assert bot._parse_onboarding_state("") is None


def test_parse_onboarding_state_returns_none_for_none(bot):
    assert bot._parse_onboarding_state(None) is None


def test_parse_onboarding_state_returns_none_for_non_dict_json(bot):
    assert bot._parse_onboarding_state('"just a string"') is None


def test_parse_onboarding_state_returns_none_for_dict_without_step(bot):
    assert bot._parse_onboarding_state(json.dumps({"cat": "backend"})) is None


def test_parse_onboarding_state_returns_dict_when_step_present(bot):
    raw = json.dumps({"step": "category"})
    result = bot._parse_onboarding_state(raw)
    assert result == {"step": "category"}


def test_parse_onboarding_state_returns_none_for_invalid_json(bot):
    assert bot._parse_onboarding_state("{not valid json") is None


# ---------------------------------------------------------------------------
# _build_onboarding_profile_text
# ---------------------------------------------------------------------------

def test_build_onboarding_profile_text_includes_category_label(bot):
    state = {"cat": "backend", "skills": [], "exp": None, "rate": None}
    text = bot._build_onboarding_profile_text(state)
    assert "Backend-разработка" in text


def test_build_onboarding_profile_text_includes_experience_label(bot):
    state = {"cat": "backend", "skills": [], "exp": "senior", "rate": None}
    text = bot._build_onboarding_profile_text(state)
    assert "Senior" in text


def test_build_onboarding_profile_text_includes_skills_when_present(bot):
    state = {"cat": "backend", "skills": ["Python", "Go"], "exp": "middle", "rate": None}
    text = bot._build_onboarding_profile_text(state)
    assert "Python" in text
    assert "Go" in text


def test_build_onboarding_profile_text_omits_rate_when_skip(bot):
    state = {"cat": "backend", "skills": [], "exp": "junior", "rate": "skip"}
    text = bot._build_onboarding_profile_text(state)
    assert "ставка" not in text.lower() or "Желаемая ставка" not in text


def test_build_onboarding_profile_text_includes_rate_when_not_skip(bot):
    state = {"cat": "backend", "skills": [], "exp": "junior", "rate": "mid"}
    text = bot._build_onboarding_profile_text(state)
    assert "1 000" in text or "ставка" in text.lower()


def test_build_onboarding_profile_text_falls_back_for_unknown_category(bot):
    state = {"cat": "unknown_cat", "skills": [], "exp": None, "rate": None}
    text = bot._build_onboarding_profile_text(state)
    assert "unknown_cat" in text


# ---------------------------------------------------------------------------
# _onboarding_start
# ---------------------------------------------------------------------------

def test_onboarding_start_sets_conversation_state_to_category_step(bot, monkeypatch):
    captured_state: list[str] = []
    send_keyboard_calls: list[dict] = []

    monkeypatch.setattr(
        bot,
        "_set_conversation_state",
        lambda tid, s: captured_state.append(s),
    )
    monkeypatch.setattr(
        bot,
        "send_keyboard",
        lambda token, chat_id, text, keyboard: send_keyboard_calls.append({"text": text, "keyboard": keyboard}),
    )

    bot._onboarding_start("bot-token", 100, 42)

    assert len(captured_state) == 1
    state = json.loads(captured_state[0])
    assert state["step"] == "category"


def test_onboarding_start_sends_keyboard_with_category_choices(bot, monkeypatch):
    send_keyboard_calls: list[dict] = []

    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: None)
    monkeypatch.setattr(
        bot,
        "send_keyboard",
        lambda token, chat_id, text, keyboard: send_keyboard_calls.append({"keyboard": keyboard}),
    )

    bot._onboarding_start("bot-token", 100, 42)

    assert len(send_keyboard_calls) == 1
    # keyboard must contain at least one ob:cat: button
    all_buttons = [
        btn
        for row in send_keyboard_calls[0]["keyboard"]
        for btn in row
    ]
    cb_datas = [b["callback_data"] for b in all_buttons]
    assert any(d.startswith("ob:cat:") for d in cb_datas)


# ---------------------------------------------------------------------------
# ob:cat: callback — category selection
# ---------------------------------------------------------------------------

def test_ob_cat_backend_transitions_to_skills_step(bot, monkeypatch):
    states: list[str] = []
    edit_calls: list[dict] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(
        bot,
        "edit_message_text",
        lambda token, chat_id, mid, text, keyboard=None: edit_calls.append({"text": text}),
    )

    _call_onboarding_handle_callback(bot, data="ob:cat:backend")

    assert len(states) == 1
    state = json.loads(states[0])
    assert state["step"] == "skills"
    assert state["cat"] == "backend"
    assert state["skills"] == []


def test_ob_cat_other_skips_skills_and_transitions_to_experience(bot, monkeypatch):
    states: list[str] = []
    edit_calls: list[dict] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(
        bot,
        "edit_message_text",
        lambda token, chat_id, mid, text, keyboard=None: edit_calls.append({"text": text}),
    )

    _call_onboarding_handle_callback(bot, data="ob:cat:other")

    assert len(states) == 1
    state = json.loads(states[0])
    assert state["step"] == "experience"


def test_ob_cat_unknown_category_is_ignored(bot, monkeypatch):
    states: list[str] = []
    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:cat:not_a_real_category")

    assert states == []


# ---------------------------------------------------------------------------
# ob:skl: callback — skill toggle
# ---------------------------------------------------------------------------

def test_ob_skl_adds_skill_to_empty_skills_list(bot, monkeypatch):
    initial = json.dumps({"step": "skills", "cat": "backend", "skills": []})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:skl:Python")

    assert len(states) == 1
    state = json.loads(states[0])
    assert "Python" in state["skills"]


def test_ob_skl_removes_skill_when_already_selected(bot, monkeypatch):
    initial = json.dumps({"step": "skills", "cat": "backend", "skills": ["Python", "Go"]})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:skl:Python")

    state = json.loads(states[0])
    assert "Python" not in state["skills"]
    assert "Go" in state["skills"]


def test_ob_skl_ignored_when_step_is_not_skills(bot, monkeypatch):
    initial = json.dumps({"step": "experience", "cat": "backend", "skills": []})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:skl:Python")

    assert states == []


# ---------------------------------------------------------------------------
# ob:skldone callback — finish skill selection
# ---------------------------------------------------------------------------

def test_ob_skldone_transitions_to_experience_step(bot, monkeypatch):
    initial = json.dumps({"step": "skills", "cat": "backend", "skills": ["Go"]})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:skldone")

    assert len(states) == 1
    state = json.loads(states[0])
    assert state["step"] == "experience"


def test_ob_skldone_ignored_when_step_is_not_skills(bot, monkeypatch):
    initial = json.dumps({"step": "experience", "cat": "backend", "skills": []})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:skldone")

    assert states == []


# ---------------------------------------------------------------------------
# ob:exp: callback — experience selection
# ---------------------------------------------------------------------------

def test_ob_exp_transitions_to_rate_step(bot, monkeypatch):
    initial = json.dumps({"step": "experience", "cat": "backend", "skills": []})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:exp:senior")

    assert len(states) == 1
    state = json.loads(states[0])
    assert state["step"] == "rate"
    assert state["exp"] == "senior"


def test_ob_exp_unknown_level_is_ignored(bot, monkeypatch):
    initial = json.dumps({"step": "experience", "cat": "backend", "skills": []})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:exp:wizard")

    assert states == []


# ---------------------------------------------------------------------------
# ob:rate: callback — rate selection
# ---------------------------------------------------------------------------

def test_ob_rate_transitions_to_confirm_step(bot, monkeypatch):
    initial = json.dumps({"step": "rate", "cat": "backend", "skills": [], "exp": "middle"})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:rate:mid")

    assert len(states) == 1
    state = json.loads(states[0])
    assert state["step"] == "confirm"
    assert state["rate"] == "mid"


def test_ob_rate_unknown_rate_is_ignored(bot, monkeypatch):
    initial = json.dumps({"step": "rate", "cat": "backend", "skills": [], "exp": "middle"})
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:rate:galactic")

    assert states == []


# ---------------------------------------------------------------------------
# ob:confirm callback — save profile
# ---------------------------------------------------------------------------

def test_ob_confirm_saves_profile_and_clears_state_on_204(bot, monkeypatch):
    initial = json.dumps({
        "step": "confirm",
        "cat": "backend",
        "skills": ["Python"],
        "exp": "senior",
        "rate": "high",
    })
    cleared: list[int] = []
    edit_calls: list[dict] = []
    send_calls: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: None)
    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: cleared.append(tid))
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: 7)
    monkeypatch.setattr(bot, "put_user_profile_status", lambda *a, **kw: 204)
    monkeypatch.setattr(
        bot,
        "edit_message_text",
        lambda token, chat_id, mid, text, keyboard=None: edit_calls.append({"text": text}),
    )
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda token, chat_id, text, **kw: send_calls.append(text),
    )
    monkeypatch.setattr(bot, "send_keyboard", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)
    monkeypatch.setattr(bot, "_maybe_send_profile_quality_hint", lambda *a: None)

    _call_onboarding_handle_callback(bot, data="ob:confirm")

    assert 42 in cleared
    assert any("Профиль сохранён" in c["text"] for c in edit_calls)


def test_ob_confirm_sends_error_on_backend_500(bot, monkeypatch):
    initial = json.dumps({
        "step": "confirm",
        "cat": "backend",
        "skills": [],
        "exp": "junior",
        "rate": "skip",
    })
    send_calls: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: None)
    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: 7)
    monkeypatch.setattr(bot, "put_user_profile_status", lambda *a, **kw: 500)
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda token, chat_id, text, **kw: send_calls.append(text),
    )
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)

    _call_onboarding_handle_callback(bot, data="ob:confirm")

    assert len(send_calls) == 1


def test_ob_confirm_sends_error_when_user_id_cannot_be_resolved(bot, monkeypatch):
    initial = json.dumps({
        "step": "confirm",
        "cat": "backend",
        "skills": [],
        "exp": "junior",
        "rate": "skip",
    })
    send_calls: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: None)
    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: None)
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda token, chat_id, text, **kw: send_calls.append(text),
    )
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)

    _call_onboarding_handle_callback(bot, data="ob:confirm")

    assert len(send_calls) == 1


def test_ob_confirm_sends_invalid_profile_message_on_400(bot, monkeypatch):
    initial = json.dumps({
        "step": "confirm",
        "cat": "backend",
        "skills": [],
        "exp": "junior",
        "rate": "skip",
    })
    send_calls: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: None)
    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: 7)
    monkeypatch.setattr(bot, "put_user_profile_status", lambda *a, **kw: 400)
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda token, chat_id, text, **kw: send_calls.append(text),
    )
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)

    _call_onboarding_handle_callback(bot, data="ob:confirm")

    assert len(send_calls) == 1
    assert any("короткий" in s or "заглушк" in s for s in send_calls)


# ---------------------------------------------------------------------------
# ob:edit callback — switch to manual text entry
# ---------------------------------------------------------------------------

def test_ob_edit_sets_step_to_edit(bot, monkeypatch):
    initial = json.dumps({"step": "confirm", "cat": "backend", "skills": [], "exp": "middle", "rate": "skip"})
    states: list[str] = []
    edit_calls: list[dict] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: initial)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(
        bot,
        "edit_message_text",
        lambda token, chat_id, mid, text, keyboard=None: edit_calls.append({"text": text}),
    )

    _call_onboarding_handle_callback(bot, data="ob:edit")

    assert len(states) == 1
    state = json.loads(states[0])
    assert state["step"] == "edit"


# ---------------------------------------------------------------------------
# ob:manual callback — manual profile entry from returning-user menu
# ---------------------------------------------------------------------------

def test_ob_manual_sets_step_to_edit(bot, monkeypatch):
    states: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: states.append(s))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)

    _call_onboarding_handle_callback(bot, data="ob:manual")

    assert len(states) == 1
    state = json.loads(states[0])
    assert state["step"] == "edit"


# ---------------------------------------------------------------------------
# ob:restart callback
# ---------------------------------------------------------------------------

def test_ob_restart_clears_state_and_starts_onboarding(bot, monkeypatch):
    cleared: list[int] = []
    onboarding_started: list[bool] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: None)
    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: cleared.append(tid))
    monkeypatch.setattr(bot, "edit_message_text", lambda *a, **kw: None)
    monkeypatch.setattr(
        bot,
        "_onboarding_start",
        lambda token, chat_id, telegram_id: onboarding_started.append(True),
    )

    _call_onboarding_handle_callback(bot, data="ob:restart")

    assert 42 in cleared
    assert onboarding_started == [True]


# ---------------------------------------------------------------------------
# ob:keep callback
# ---------------------------------------------------------------------------

def test_ob_keep_clears_state_and_sends_no_change_message(bot, monkeypatch):
    cleared: list[int] = []
    edit_calls: list[dict] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_set_conversation_state", lambda tid, s: None)
    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: cleared.append(tid))
    monkeypatch.setattr(
        bot,
        "edit_message_text",
        lambda token, chat_id, mid, text, keyboard=None: edit_calls.append({"text": text}),
    )

    _call_onboarding_handle_callback(bot, data="ob:keep")

    assert 42 in cleared
    assert any("не изменён" in c["text"] for c in edit_calls)


# ---------------------------------------------------------------------------
# handle_message — /start triggers onboarding for new users
# ---------------------------------------------------------------------------

def test_start_command_triggers_onboarding_for_new_user(bot, monkeypatch):
    onboarding_started: list[bool] = []

    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "post_users", lambda *a, **kw: 99)
    monkeypatch.setattr(bot, "_cache_user_id", lambda tid, uid: None)
    monkeypatch.setattr(bot, "_mark_first_seen", lambda tid: True)
    monkeypatch.setattr(
        bot,
        "_onboarding_start",
        lambda token, chat_id, telegram_id: onboarding_started.append(True),
    )
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)

    update = _make_update(text="/start")
    result = _call_handle_update(bot, update)

    assert result is True
    assert onboarding_started == [True]


def test_start_command_does_not_trigger_onboarding_for_returning_user(bot, monkeypatch):
    import time as _time
    onboarding_started: list[bool] = []
    send_keyboard_calls: list[dict] = []

    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "post_users", lambda *a, **kw: 99)
    monkeypatch.setattr(bot, "_cache_user_id", lambda tid, uid: None)
    monkeypatch.setattr(bot, "_mark_first_seen", lambda tid: False)
    monkeypatch.setattr(bot, "_get_first_seen_ts", lambda tid: _time.time() - 1)
    monkeypatch.setattr(
        bot,
        "_onboarding_start",
        lambda token, chat_id, telegram_id: onboarding_started.append(True),
    )
    monkeypatch.setattr(
        bot,
        "send_keyboard",
        lambda token, chat_id, text, keyboard: send_keyboard_calls.append(text),
    )
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)

    update = _make_update(text="/start")
    result = _call_handle_update(bot, update)

    assert result is True
    assert onboarding_started == []
    assert len(send_keyboard_calls) > 0


def test_start_command_sends_error_when_post_users_fails(bot, monkeypatch):
    send_calls: list[str] = []

    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "post_users", lambda *a, **kw: None)
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda token, chat_id, text, **kw: send_calls.append(text),
    )
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)

    update = _make_update(text="/start")
    result = _call_handle_update(bot, update)

    assert result is True
    assert len(send_calls) == 1


# ---------------------------------------------------------------------------
# handle_message — ob:edit step processes free-text profile
# ---------------------------------------------------------------------------

def test_message_in_edit_step_submits_profile(bot, monkeypatch):
    ob_state = json.dumps({"step": "edit", "cat": "backend", "skills": [], "exp": "middle", "rate": "skip"})
    profile_submissions: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: ob_state)
    monkeypatch.setattr(
        bot,
        "_handle_profile_submission",
        lambda token, chat_id, tid, text, api_url, api_auth_token, api_hmac: profile_submissions.append(text) or True,
    )

    update = _make_update(text="Python backend developer, 5 years")
    result = _call_handle_update(bot, update)

    assert result is True
    assert profile_submissions == ["Python backend developer, 5 years"]


def test_command_in_edit_step_is_not_treated_as_profile(bot, monkeypatch):
    ob_state = json.dumps({"step": "edit", "cat": "backend", "skills": [], "exp": "middle", "rate": "skip"})
    profile_submissions: list[str] = []

    monkeypatch.setattr(bot, "_get_conversation_state", lambda tid: ob_state)
    monkeypatch.setattr(
        bot,
        "_handle_profile_submission",
        lambda token, chat_id, tid, text, api_url, api_auth_token, api_hmac: profile_submissions.append(text) or True,
    )
    monkeypatch.setattr(bot, "_clear_conversation_state", lambda tid: None)
    monkeypatch.setattr(bot, "_resolve_user_id", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "send_message", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_record_command", lambda *a: None)

    update = _make_update(text="/help")
    _call_handle_update(bot, update)

    assert profile_submissions == []
