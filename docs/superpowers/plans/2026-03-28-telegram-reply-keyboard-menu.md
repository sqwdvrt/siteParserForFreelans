# Telegram Reply Keyboard Menu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent Reply Keyboard so users can access core bot actions via visible buttons instead of text commands.

**Architecture:** Add two helpers (`_build_main_reply_keyboard`, `send_with_reply_keyboard`), wire 5 text handlers + 2 callback handlers (`menu:settings`, `menu:notify_hour`) into existing `_handle_update` and `handle_callback`, then attach the keyboard to onboarding completion, `/start`, `/help`, `/stats` responses.

**Tech Stack:** Python 3.11, python-telegram-bot HTTP API (raw), pytest. All changes in `telegram-bot/main.py`.

---

## Files

- Modify: `telegram-bot/main.py` — add helpers, handlers, attach keyboard
- Modify: `telegram-bot/tests/test_webhook.py` — add tests for new text handlers and callbacks

---

### Task 1: Add `_build_main_reply_keyboard` and `send_with_reply_keyboard` helpers

**Files:**
- Modify: `telegram-bot/main.py` — add two functions after `_build_returning_user_keyboard` (~line 1630)

- [ ] **Step 1: Read the insertion point**

Open `telegram-bot/main.py` and find `_build_returning_user_keyboard` (around line 1625). New helpers go directly after it.

- [ ] **Step 2: Add helpers**

Insert after `_build_returning_user_keyboard`:

```python
def _build_main_reply_keyboard() -> dict:
    """Persistent Reply Keyboard shown to registered users."""
    return {
        "keyboard": [
            [{"text": "👤 Мой профиль"}, {"text": "📊 Статистика"}],
            [{"text": "✏️ Обновить профиль"}, {"text": "⚙️ Настройки"}],
            [{"text": "❓ Помощь"}],
        ],
        "resize_keyboard": True,
        "persistent": True,
        "is_persistent": True,
    }


def send_with_reply_keyboard(token: str, chat_id: int, text: str, *, parse_html: bool = False) -> None:
    """sendMessage with the main Reply Keyboard attached."""
    url = f"{TELEGRAM_BASE}{token}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": _build_main_reply_keyboard(),
    }
    if parse_html:
        payload["parse_mode"] = "HTML"
    status, data = _http_post(url, payload)
    if status != 200:
        detail = ""
        if isinstance(data, dict):
            description = (data.get("description") or "")
            if isinstance(description, str) and description.strip():
                detail = f" detail={_compact_log_text(description)}"
        logger.warning("send_with_reply_keyboard failed: status=%s%s", status, detail)
```

- [ ] **Step 3: Commit**

```bash
git add telegram-bot/main.py
git commit -m "feat: add _build_main_reply_keyboard and send_with_reply_keyboard helpers"
```

---

### Task 2: Add `get_user_profile_text` API helper

**Files:**
- Modify: `telegram-bot/main.py` — add after `get_user_is_pro` (~line 1080)

The "👤 Мой профиль" button needs to fetch the user's current profile text. Add a `GET /users/:id` wrapper.

- [ ] **Step 1: Check existing GET /users/:id in the API**

In `backend/internal/api/`, confirm `GET /users/:id` returns `profile_text`. It does — the existing `/users` response includes `profile_text`.

- [ ] **Step 2: Add the function**

Insert after `get_user_is_pro`:

```python
def get_user_profile_text(
    api_url: str,
    user_id: int,
    telegram_id: int,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> str | None:
    """GET /users/:id — returns profile_text or None on error."""
    url = f"{api_url.rstrip('/')}/users/{user_id}"
    body = b""
    data = _http_get(
        url,
        {},
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "GET", url, telegram_id, body),
    )
    if not isinstance(data, dict):
        return None
    return data.get("profile_text") or ""
```

- [ ] **Step 3: Commit**

```bash
git add telegram-bot/main.py
git commit -m "feat: add get_user_profile_text API helper"
```

---

### Task 3: Add text handlers for 5 Reply Keyboard buttons

**Files:**
- Modify: `telegram-bot/main.py` — add inside `_handle_update`, before the "unrecognized text" fallback (~line 2215)

- [ ] **Step 1: Find insertion point**

In `_handle_update`, find the last `if text == "/notify_hour"` block. After its `return`, add the new handlers.

- [ ] **Step 2: Add handlers**

```python
    if text == "👤 Мой профиль":
        _clear_conversation_state(telegram_id)
        user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is None:
            send_message(token, chat_id, "Сначала отправьте /start")
            return True
        profile_text = get_user_profile_text(api_url, user_id, telegram_id, api_auth_token, api_user_hmac_secret)
        if profile_text is None:
            send_message(token, chat_id, "Не удалось загрузить профиль. Попробуйте позже.")
        elif profile_text == "":
            send_message(token, chat_id, "Профиль не заполнен. Нажмите ✏️ Обновить профиль.")
        else:
            send_message(token, chat_id, f"👤 Ваш профиль:\n\n{profile_text}")
        return True

    if text == "📊 Статистика":
        text = "/stats"
        # fall through to /stats handler below — re-dispatch

    if text == "✏️ Обновить профиль":
        _set_conversation_state(telegram_id, "await_profile")
        _record_command("profile", "prompt")
        send_message(
            token,
            chat_id,
            "Отправьте следующим сообщением текст профиля.",
        )
        return True

    if text == "⚙️ Настройки":
        send_keyboard(
            token,
            chat_id,
            "⚙️ Настройки",
            [[{"text": "🕐 Час уведомлений", "callback_data": "menu:notify_hour"}]],
        )
        return True

    if text == "❓ Помощь":
        text = "/help"
        # fall through to /help handler below — re-dispatch
```

Note: `📊 Статистика` and `❓ Помощь` set `text` to the matching command and fall through — no `return True` — so existing `/stats` and `/help` handlers fire naturally.

- [ ] **Step 3: Write tests**

In `telegram-bot/tests/test_webhook.py`, add:

```python
def test_menu_button_my_profile_empty(mock_bot_env):
    """👤 Мой профиль when profile is empty shows prompt."""
    responses = [
        (200, {"id": 42}),   # POST /users
        (200, {"profile_text": ""}),  # GET /users/42
    ]
    # patch _http_post / _http_get_authed to return responses...
    # assert send_message called with "Профиль не заполнен"

def test_menu_button_update_profile(mock_bot_env):
    """✏️ Обновить профиль sets await_profile state."""
    # send text "✏️ Обновить профиль"
    # assert conversation state = "await_profile"

def test_menu_button_settings(mock_bot_env):
    """⚙️ Настройки sends inline keyboard with notify_hour button."""
    # assert send_keyboard called with callback_data="menu:notify_hour"
```

- [ ] **Step 4: Run tests**

```bash
cd telegram-bot && python -m pytest tests/test_webhook.py -v -k "menu_button"
```

Expected: tests pass or at least execute without import errors.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/main.py telegram-bot/tests/test_webhook.py
git commit -m "feat: add text handlers for Reply Keyboard buttons"
```

---

### Task 4: Add `menu:notify_hour` and `menu:settings` callback handlers

**Files:**
- Modify: `telegram-bot/main.py` — inside `handle_callback`, add `elif data.startswith("menu:")` branch

- [ ] **Step 1: Find insertion point**

In `handle_callback` (~line 1890), find the `elif data.startswith("fb:")` block. Add a new `elif` before it:

```python
        elif data.startswith("menu:") and telegram_id is not None:
            msg = callback.get("message") or {}
            cb_chat_id = msg.get("chat", {}).get("id") or from_user.get("id")
            if cb_chat_id:
                _menu_handle_callback(
                    data, token, cb_chat_id, telegram_id,
                    api_url, api_auth_token, api_user_hmac_secret,
                )
```

- [ ] **Step 2: Add `_menu_handle_callback`**

Insert before `handle_callback`:

```python
def _menu_handle_callback(
    data: str,
    token: str,
    chat_id: int,
    telegram_id: int,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> None:
    """Handle menu: callback_data from Settings submenu."""
    if data == "menu:notify_hour":
        user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is None:
            send_message(token, chat_id, "Сначала отправьте /start")
            return
        is_pro = get_user_is_pro(api_url, user_id, telegram_id, api_auth_token, api_user_hmac_secret)
        if is_pro is False:
            send_message(token, chat_id, "⛔ Выбор часа доступен только Pro-пользователям.")
            return
        _set_conversation_state(telegram_id, "await_notify_hour")
        _record_command("notify_hour", "prompt")
        send_message(
            token,
            chat_id,
            "Отправьте следующим сообщением час от 0 до 23 (по МСК).",
        )
```

- [ ] **Step 3: Commit**

```bash
git add telegram-bot/main.py
git commit -m "feat: add menu:notify_hour callback handler"
```

---

### Task 5: Attach Reply Keyboard to existing flows

**Files:**
- Modify: `telegram-bot/main.py` — 4 attachment points

- [ ] **Step 1: Onboarding completion (ob:confirm success, ~line 1756)**

Replace the `send_message` after `"Как только появятся подходящие заказы..."` with `send_with_reply_keyboard`:

```python
            send_with_reply_keyboard(
                token,
                chat_id,
                "Как только появятся подходящие заказы — уведомлю вас.\n"
                "Нажимайте 👍/👎 под заказами, чтобы обучить алгоритм.",
            )
```

- [ ] **Step 2: /start returning user — days ≥ 7 (~line 2081)**

The existing `send_keyboard` shows inline buttons "🔄 Пройти анкету" / "Оставить текущий профиль". Keep it — inline keyboards and Reply Keyboards coexist. Add a `send_with_reply_keyboard` after:

```python
                    send_with_reply_keyboard(
                        token, chat_id,
                        "Меню доступно в любой момент 👇",
                    )
```

- [ ] **Step 3: /start returning user — days < 7 (~line 2090)**

Similarly after existing `send_keyboard(_build_returning_user_keyboard())`:

```python
                    send_with_reply_keyboard(
                        token, chat_id,
                        "Меню доступно в любой момент 👇",
                    )
```

- [ ] **Step 4: /help response (~line 2127)**

Replace `send_message(token, chat_id, ...)` with `send_with_reply_keyboard(token, chat_id, ..., parse_html=True)`.

- [ ] **Step 5: /stats response (~line 2170)**

Replace `send_message(token, chat_id, ...)` at the end of stats handler with `send_with_reply_keyboard(token, chat_id, ...)`.

- [ ] **Step 6: Commit**

```bash
git add telegram-bot/main.py
git commit -m "feat: attach Reply Keyboard to onboarding, /start, /help, /stats"
```

---

### Task 6: Run full test suite and deploy

- [ ] **Step 1: Run all tests**

```bash
cd /path/to/repo && bash scripts/pytest_telegram_bot.sh
```

Expected: all 161+ tests pass.

- [ ] **Step 2: Build image on VPS**

```bash
ssh deploy@185.154.193.193 "cd /home/deploy/app/siteParserForFreelans && git pull origin develop && docker build -t telegram-bot:menu -f telegram-bot/Dockerfile telegram-bot/"
```

- [ ] **Step 3: Update image tag and restart**

```bash
ssh deploy@185.154.193.193 "cd /home/deploy/app/siteParserForFreelans && sed -i 's|TELEGRAM_BOT_IMAGE=.*|TELEGRAM_BOT_IMAGE=telegram-bot:menu|' .env.production && docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml up -d --no-deps --force-recreate telegram-bot"
```

- [ ] **Step 4: Verify no errors in logs**

```bash
ssh deploy@185.154.193.193 "sleep 5 && docker logs --tail=20 siteparserforfreelans-telegram-bot-1"
```

Expected: startup INFO lines, no ERROR/TypeError.
