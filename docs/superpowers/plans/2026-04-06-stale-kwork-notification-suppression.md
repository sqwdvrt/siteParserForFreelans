# Stale Kwork Notification Suppression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop users from receiving Kwork projects whose status has not been confirmed for more than 6 hours, without immediately expiring those jobs from the catalog.

**Architecture:** Add a source-aware freshness policy as a defense-in-depth gate at notification delivery time, and mirror the same stale Kwork exclusion in AI match queries so stale jobs stop entering new notification flows. Expose `last_seen_at` on backend job reads so delivery decisions use crawler-confirmed freshness rather than coarse job age.

**Tech Stack:** Go backend, Python ai-service, PostgreSQL, unittest/pytest, Docker/VPS deploy pipeline

---

### Task 1: Backend stale-delivery gate

**Files:**
- Modify: `backend/internal/domain/job.go`
- Modify: `backend/internal/adapter/postgres/repository.go`
- Create: `backend/internal/usecase/job_freshness_policy.go`
- Modify: `backend/internal/usecase/send_notification.go`
- Modify: `backend/internal/usecase/daily_digest.go`
- Test: `backend/internal/usecase/send_notification_test.go`
- Test: `backend/internal/usecase/daily_digest_test.go`

- [ ] Write failing tests proving stale `kwork` jobs are skipped in single-send and digest paths.
- [ ] Run targeted Go tests and confirm the new tests fail for the expected reason.
- [ ] Add `LastSeenAt` to `domain.Job` and load it from Postgres reads.
- [ ] Implement a focused freshness policy helper with a `6h` stale threshold for `kwork`.
- [ ] Apply the policy in `SendNotification` and `DailyDigest`, deleting pending notifications when the job is stale.
- [ ] Re-run targeted Go tests until green.

### Task 2: AI stale-match suppression

**Files:**
- Modify: `ai-service/src/ai_service/adapter/postgres/match_repository.py`
- Test: `ai-service/tests/test_match_repository.py`
- Test: `ai-service/tests/test_match_repository_unit.py`

- [ ] Write failing tests proving stale active `kwork` jobs are excluded by default from `find_users_for_job` and `find_jobs_for_user`.
- [ ] Run targeted pytest and confirm the new tests fail first.
- [ ] Add the SQL predicate that excludes `kwork` rows with `last_seen_at < NOW() - INTERVAL '6 hours'`.
- [ ] Re-run targeted pytest until green.

### Task 3: Verification and VPS rollout

**Files:**
- Verify only: production deploy workflow and live VPS release

- [ ] Run focused backend and ai-service verification commands locally.
- [ ] Commit and push the fix set.
- [ ] Update the VPS to the new release.
- [ ] Verify on VPS that the live code contains the freshness gate and that public health remains green.
