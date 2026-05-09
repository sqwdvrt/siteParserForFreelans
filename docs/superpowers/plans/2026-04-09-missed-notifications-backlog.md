# Missed Notifications Backlog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve high-scoring jobs that were skipped because of rate or daily limits by moving them into a `missed` backlog and rolling them back into `pending` later.

**Architecture:** Add a dedicated `missed` notification state with repository methods to mark, list, convert, and delete backlog items. `SendNotification` will mark newly limited items as `missed` instead of deleting them, and `DailyDigest` will pull top-scoring `missed` items back into `pending` before claiming digest items.

**Tech Stack:** Go, Postgres, pgx, existing backend unit and integration tests

---

### Task 1: Cover missed-notification repository behavior

**Files:**
- Create: none
- Modify: `backend/internal/adapter/postgres/notification_repository_test.go`
- Modify: `backend/internal/port/notification_repository.go`
- Modify: `backend/migrations/030_notifications_missed.sql`
- Modify: `backend/migrations/manifest.txt`

- [x] Add failing integration tests for `MarkMissed`, `GetMissedForUser`, `ConvertMissedToPending`, and `DeleteNotifications`.
- [x] Run targeted notification repository integration tests and verify they fail because the new methods are missing.
- [x] Implement the missing interface surface and SQL migration wiring.
- [ ] Re-run the targeted integration tests until they pass. Blocked locally: Docker daemon is not running, so the required Postgres test container could not be started.

### Task 2: Preserve limited notifications instead of deleting them

**Files:**
- Modify: `backend/internal/usecase/send_notification.go`
- Modify: `backend/internal/usecase/send_notification_test.go`

- [x] Add failing unit tests proving that new notifications become `missed` on rate-limit and daily-limit paths.
- [x] Run targeted send-notification tests and verify the new expectations fail.
- [x] Replace delete-on-limit logic with `MarkMissed` for newly inserted notifications while keeping retries unchanged.
- [x] Re-run targeted send-notification tests until they pass.

### Task 3: Roll missed backlog back into digest delivery

**Files:**
- Modify: `backend/internal/usecase/daily_digest.go`
- Modify: `backend/internal/usecase/daily_digest_test.go`

- [x] Add failing unit tests proving `DailyDigest` converts top-scoring `missed` notifications into `pending` before claiming digest items.
- [x] Run targeted digest tests and verify the new expectations fail.
- [x] Implement rollover logic that selects only fresh/active missed items and converts them up to the remaining daily limit.
- [x] Re-run targeted digest tests until they pass.

### Task 4: Full verification and release prep

**Files:**
- Modify: none

- [ ] Run `go test ./internal/usecase ./internal/adapter/postgres ./cmd/notifier -count=1`. Blocked locally for `./internal/adapter/postgres -tags=integration`: Docker daemon is stopped, so no Postgres container is available. `./internal/usecase` and `./cmd/notifier` passed.
- [x] Run full `go test ./... -count=1`.
- [ ] Commit the finished backlog feature with migration and tests.
