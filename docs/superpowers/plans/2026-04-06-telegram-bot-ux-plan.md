# Telegram Bot UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Telegram bot from a thin profile-entry client into a user-manageable freelancer assistant with clear onboarding, filters, status, pause, and Pro surfaces.

**Architecture:** Reuse the existing Telegram onboarding state machine, reply keyboard, `/users/{id}/preferences`, and `/users/{id}/stats` APIs instead of rebuilding flows from scratch. Split work into two tracks: bot-only UX improvements that can ship quickly, and backend-supported capabilities such as pause that require schema/API/notifier changes.

**Tech Stack:** Python Telegram bot client, Go backend API/notifier, PostgreSQL, Redis, pytest, Go test.

---

## Current-State Findings

- `telegram-bot` already has a real onboarding wizard via `ob:*` callbacks in `telegram-bot/main.py`.
- `/start`, `/profile`, `/stats`, `/notify_hour`, and `/help` already exist in `telegram-bot/main.py`.
- The reply keyboard already exists, but it is too thin and does not expose filters, pause, or Pro clearly.
- Project cards already include an `Открыть проект` URL button in `telegram-bot/main.py`; this should be kept and made more prominent in copy, not re-implemented.
- Backend already supports `GET/PUT /users/{id}/preferences` and `GET /users/{id}/stats`.
- Backend does **not** currently expose a pause capability for notifications. `/pause` is a real backend feature, not a bot-only shortcut.

## Scope Split

### Fast Track: Bot + existing backend contracts

- `/start` returning-user menu redesign
- richer `/profile`
- `/filters`
- `/status` productized alias over existing `/stats`
- move notify-hour into settings and hide `/notify_hour` from public command list
- `/pro` surface

### Backend Track: new contracts required

- `/pause`
- pause-aware notifier and digest behavior
- optional reminder / resume flow for paused users

## Recommended Release Order

1. Navigation and terminology cleanup
2. Filters and onboarding v2
3. Status and Pro surfaces
4. Pause backend + bot flow

## File Map

**Primary bot files**

- Modify: `telegram-bot/main.py`
- Modify: `telegram-bot/tests/test_polling.py`
- Modify: `telegram-bot/tests/test_api_helpers.py`
- Modify: `telegram-bot/tests/test_onboarding.py`

**Backend files already relevant to preferences/stats**

- Modify: `backend/internal/api/handlers.go`
- Modify: `backend/internal/api/handlers_test.go`
- Modify: `backend/internal/adapter/postgres/user_stats_repository.go`

**Backend files for new pause capability**

- Modify: `backend/internal/domain/user.go` or the effective user domain file used by API/repository
- Modify: `backend/internal/port/user_repository.go`
- Modify: `backend/internal/adapter/postgres/user_repository.go`
- Modify: `backend/internal/usecase/send_notification.go`
- Modify: `backend/internal/usecase/daily_digest.go`
- Modify: `backend/cmd/api/main.go`
- Modify: `backend/cmd/notifier/main.go`
- Create: `backend/migrations/<new_pause_migration>.sql`

**Docs**

- Modify: `README.md`
- Modify: `docs/operations.md`

## Product Decisions Locked In

- Keep `/stats` as a backward-compatible alias, but make `/status` the primary user-facing command and wording.
- Keep `Открыть проект`; do not spend time re-adding what already exists.
- Remove `/notify_hour` from the visible command list, but keep the command working as a hidden power-user alias.
- Reuse the existing onboarding engine instead of replacing it with a separate wizard framework.
- Ship `/filters` before `/pause`, because filters are already supported by backend and unblock user control faster.

### Task 1: Clean Up Command IA And Main Menu

**Files:**
- Modify: `telegram-bot/main.py`
- Test: `telegram-bot/tests/test_polling.py`

- [ ] **Step 1: Write failing tests for the new public command surface**

Add tests that assert:
- `set_my_commands()` registers `/start`, `/profile`, `/filters`, `/status`, `/pause`, `/help`, `/pro`
- `/notify_hour` is not in public commands
- `/stats` still works as an alias

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd telegram-bot && pytest -q tests/test_polling.py -k 'commands or status'`
Expected: FAIL because the current command list still exposes `/notify_hour` and has no `/filters`, `/status`, `/pause`, `/pro`

- [ ] **Step 3: Implement minimal command and menu reshaping**

In `telegram-bot/main.py`:
- update `set_my_commands()`
- add `/status` aliasing the current stats flow
- add `/filters`, `/pause`, `/pro` handlers as stubs or real flows depending on later tasks
- expand the reply keyboard and settings menu to match the new IA

- [ ] **Step 4: Run targeted tests**

Run: `cd telegram-bot && pytest -q tests/test_polling.py -k 'commands or status'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/main.py telegram-bot/tests/test_polling.py
git commit -m "Reshape telegram bot command IA"
```

### Task 2: Upgrade `/start` And Returning-User Home

**Files:**
- Modify: `telegram-bot/main.py`
- Test: `telegram-bot/tests/test_onboarding.py`
- Test: `telegram-bot/tests/test_polling.py`

- [ ] **Step 1: Write failing tests for `/start` home behavior**

Add tests that cover:
- new user still enters onboarding
- returning user sees a real home screen instead of a generic “already registered” message
- home screen exposes Settings, Status, Pause, Pro, Help

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd telegram-bot && pytest -q tests/test_onboarding.py tests/test_polling.py -k 'start or returning'`
Expected: FAIL because current returning-user UX still points to the old restart/manual/keep choice

- [ ] **Step 3: Reuse the existing onboarding flow, but change entry framing**

Implement:
- `/start` for new users: friendlier intro copy
- `/start` for returning users: home dashboard with stats summary and action buttons
- keep `ob:*` callbacks; do not fork onboarding logic into a second state machine

- [ ] **Step 4: Run targeted tests**

Run: `cd telegram-bot && pytest -q tests/test_onboarding.py tests/test_polling.py -k 'start or returning'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/main.py telegram-bot/tests/test_onboarding.py telegram-bot/tests/test_polling.py
git commit -m "Upgrade telegram bot start and home flows"
```

### Task 3: Ship `/filters` On Top Of Existing Preferences API

**Files:**
- Modify: `telegram-bot/main.py`
- Test: `telegram-bot/tests/test_api_helpers.py`
- Test: `telegram-bot/tests/test_polling.py`
- Optional modify: `backend/internal/api/handlers.go`
- Optional test: `backend/internal/api/handlers_test.go`

- [ ] **Step 1: Write failing bot tests for filters UX**

Cover:
- `/filters` loads current preferences
- source selection flow
- min budget update flow
- include keyword update flow
- exclude keyword update flow
- reset flow

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd telegram-bot && pytest -q tests/test_api_helpers.py tests/test_polling.py -k 'filters or preferences'`
Expected: FAIL because the bot has API helpers but no conversational filters UI

- [ ] **Step 3: Add missing API helper only if needed**

Reuse existing `get_user_preferences()` and add a matching `put_user_preferences()` helper if one is missing in `telegram-bot/main.py`.

- [ ] **Step 4: Implement `/filters` state machine**

Implement a compact flow:
- root overview screen
- sources picker
- budget editor
- include keywords editor
- exclude keywords editor
- reset action

Keep it callback-driven where possible to avoid free-form parsing everywhere.

- [ ] **Step 5: Extend onboarding v2 with only two high-impact preference steps**

Add to onboarding:
- preferred sources
- min budget

Do **not** add every filter to onboarding; keep first-run friction low.

- [ ] **Step 6: Run bot tests**

Run: `cd telegram-bot && pytest -q tests/test_api_helpers.py tests/test_polling.py tests/test_onboarding.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add telegram-bot/main.py telegram-bot/tests/test_api_helpers.py telegram-bot/tests/test_polling.py telegram-bot/tests/test_onboarding.py
git commit -m "Add telegram bot filters flow"
```

### Task 4: Upgrade `/profile`, `/status`, And `/pro`

**Files:**
- Modify: `telegram-bot/main.py`
- Test: `telegram-bot/tests/test_polling.py`
- Optional modify: `backend/internal/adapter/postgres/user_stats_repository.go`
- Optional test: `backend/internal/api/handlers_test.go`

- [ ] **Step 1: Write failing tests for richer profile/status/pro copy**

Cover:
- `/profile` shows current profile + action buttons
- `/status` is the primary label, `/stats` remains alias
- `/pro` shows free vs pro capability summary

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd telegram-bot && pytest -q tests/test_polling.py -k 'profile or stats or pro or status'`
Expected: FAIL because current UX is still utilitarian

- [ ] **Step 3: Implement productized messaging**

Implement:
- profile card with “edit” and “tips”
- status screen using current backend stats payload, with clearer user wording
- Pro explanation screen

If the current stats payload is too thin for “today vs 7 days”, add fields in backend stats response, but do that only after confirming the existing payload is insufficient.

- [ ] **Step 4: Run targeted tests**

Run: `cd telegram-bot && pytest -q tests/test_polling.py -k 'profile or stats or pro or status'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/main.py telegram-bot/tests/test_polling.py
git commit -m "Improve telegram bot profile status and pro UX"
```

### Task 5: Add Pause As A First-Class Backend Capability

**Files:**
- Create: `backend/migrations/<timestamp>_users_pause.sql`
- Modify: `backend/internal/port/user_repository.go`
- Modify: `backend/internal/adapter/postgres/user_repository.go`
- Modify: `backend/internal/api/handlers.go`
- Modify: `backend/internal/api/handlers_test.go`
- Modify: `backend/internal/usecase/send_notification.go`
- Modify: `backend/internal/usecase/daily_digest.go`
- Modify: `backend/cmd/api/main.go`

- [ ] **Step 1: Write failing backend tests for pause semantics**

Cover:
- pause-until timestamp persists
- paused users are skipped by send notification use case
- paused users are skipped by digest use case
- resume clears pause

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && go test ./internal/api ./internal/usecase ./internal/adapter/postgres -count=1`
Expected: FAIL because pause does not exist yet

- [ ] **Step 3: Add the data model**

Preferred shape:
- `users.paused_until timestamptz null`

Avoid a separate boolean unless a specific query pattern requires it.

- [ ] **Step 4: Add API contract**

Expose either:
- `PUT /users/{id}/pause`
- `DELETE /users/{id}/pause`

or
- `PUT /users/{id}/pause` with payload `{ "until": "...RFC3339..." }`

Recommendation: single `PUT` plus nullable `until`, because it keeps the bot simpler.

- [ ] **Step 5: Make delivery paths honor pause**

In notifier/digest use cases:
- check user pause state before claiming/sending
- treat paused as a non-error skip

- [ ] **Step 6: Run backend tests**

Run: `cd backend && go test ./internal/api ./internal/usecase ./internal/adapter/postgres -count=1`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend
git commit -m "Add pause support for user notifications"
```

### Task 6: Expose `/pause` In The Bot

**Files:**
- Modify: `telegram-bot/main.py`
- Test: `telegram-bot/tests/test_polling.py`

- [ ] **Step 1: Write failing bot tests for pause flow**

Cover:
- `/pause` shows duration choices
- selecting a duration calls the backend pause endpoint
- resume flow works

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd telegram-bot && pytest -q tests/test_polling.py -k 'pause'`
Expected: FAIL because bot has no pause UX

- [ ] **Step 3: Implement callback-driven pause flow**

Implement:
- `/pause`
- duration options: 1 day, 3 days, 7 days, forever
- resume/unpause entry point

- [ ] **Step 4: Run bot tests**

Run: `cd telegram-bot && pytest -q tests/test_polling.py -k 'pause'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/main.py telegram-bot/tests/test_polling.py
git commit -m "Add pause flow to telegram bot"
```

## Deferred Items

- `🤔 Объяснение неточное` feedback path
- pause reminder jobs
- fully split “today” vs “7 days” stats if current payload proves insufficient
- billing / paywall mechanics behind Pro

## Success Criteria

- New users understand how to configure the bot without reading `/help`
- Returning users can control profile, filters, status, pause, and Pro from one home surface
- Filters are editable from the bot without leaving Telegram
- `/notify_hour` still works, but is no longer the public mental model
- Notification pause is enforced server-side, not just hidden in the bot UI
- Existing project-card navigation and feedback continue working

## Recommended Delivery Sequence

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6

This order ships user-visible value early and postpones only the one feature that truly needs backend contract changes.
