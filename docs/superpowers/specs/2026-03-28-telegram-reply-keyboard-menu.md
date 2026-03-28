# Spec: Telegram Reply Keyboard Menu

**Date:** 2026-03-28
**Status:** Approved

## Summary

Add a persistent Reply Keyboard to the Telegram bot so users can access core actions via visible buttons instead of memorizing text commands.

## Layout

```
[ 👤 Мой профиль ]  [ 📊 Статистика ]
[ ✏️ Обновить профиль ]  [ ⚙️ Настройки ]
             [ ❓ Помощь ]
```

## Button Behaviour

| Button | Action |
|---|---|
| 👤 Мой профиль | Fetch profile_text from API and display it. If empty — "Профиль не заполнен. Нажмите ✏️ Обновить профиль." |
| 📊 Статистика | Same logic as `/stats` command |
| ✏️ Обновить профиль | Set conversation state to `await_profile`, prompt user to send profile text |
| ⚙️ Настройки | Send inline submenu (see below) |
| ❓ Помощь | Same logic as `/help` command |

## Settings Submenu (Inline Keyboard)

Sent as a separate message with inline buttons:

```
⚙️ Настройки

[ 🕐 Час уведомлений ]
```

Pressing **🕐 Час уведомлений** triggers the existing `notify_hour` flow (Pro check → hour selection via inline buttons 0–23).

New `callback_data` values:
- `menu:settings` — show settings submenu
- `menu:notify_hour` — enter notify_hour selection flow

## When the Keyboard Appears

The Reply Keyboard is sent (or re-sent) in these situations:

1. New user completes onboarding wizard (after ob:confirm)
2. Returning user on `/start` (both cases: days ≥ 7 and days < 7)
3. After `/start` error recovery
4. After `/help` command
5. After `/stats` command

The keyboard uses `resize_keyboard: true` and `persistent: true` so it stays visible.

## Implementation Scope

- Add `_build_main_reply_keyboard()` helper returning the Reply Keyboard dict
- Add `send_with_reply_keyboard()` helper wrapping sendMessage with `reply_markup`
- Add text handlers for the 5 button labels (match exact strings)
- Add `callback_data` handlers: `menu:settings`, `menu:notify_hour`
- Attach keyboard to existing onboarding completion, `/start`, `/help`, `/stats` responses
- No changes to onboarding wizard internals or feedback (👍/👎) flow

## Out of Scope

- No new API endpoints required
- No database changes
- No changes to notification format
