# Residual Risk Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining backend best-effort cleanup risk on immediate `404/410` expiry and make production monitoring default to `REDIS_URL` before falling back to the dev Redis address.

**Architecture:** Use a narrow transactional backend path so immediate job expiry and pending-notification cleanup succeed or fail together. For monitoring, keep the existing overlay shape but change Redis exporter address resolution order to `REDIS_EXPORTER_REDIS_ADDR` -> `REDIS_URL` -> dev fallback, then verify via `docker compose config`.

**Tech Stack:** Go, PostgreSQL, Docker Compose, Markdown docs

---

### Task 1: Make Immediate `404/410` Cleanup Atomic

**Files:**
- Modify: `backend/internal/usecase/crawl_projects.go`
- Modify: `backend/internal/usecase/crawl_projects_test.go`
- Modify: `backend/internal/adapter/postgres/repository.go`
- Modify: `backend/internal/port/repository.go`
- Modify: any backend test doubles affected by the repository interface

- [ ] **Step 1: Write the failing test**
  Add a regression test proving that when immediate expiry cleanup fails, the crawler does not leave the system in a partially updated state.

- [ ] **Step 2: Run the focused test to verify it fails**
  Run: `cd backend && GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/usecase -run TestCrawlProjects_ -count=1`
  Expected: the new case fails for the intended atomicity gap.

- [ ] **Step 3: Implement the minimal transactional repository path**
  Make the immediate gone-path use one Postgres transaction for `jobs.status='expired'` and pending-notification deletion.

- [ ] **Step 4: Update interface consumers and test doubles**
  Keep signatures coherent across usecases and tests.

- [ ] **Step 5: Re-run focused backend tests**
  Run: `cd backend && GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/usecase -count=1`
  Expected: pass.

### Task 2: Make Monitoring Redis Exporter Default to `REDIS_URL`

**Files:**
- Modify: `docker-compose.monitoring.yml`
- Modify: `README.md`
- Modify: `.env.production.example`

- [ ] **Step 1: Add or refine the failing config expectation**
  Capture the intended precedence: `REDIS_EXPORTER_REDIS_ADDR` first, then `REDIS_URL`, then `redis://redis:6379`.

- [ ] **Step 2: Run render verification on current config**
  Run: `docker compose --env-file .env.production.example -f docker-compose.prod.yml -f docker-compose.monitoring.yml --profile monitoring config`
  Expected: current output still resolves `redis-exporter` incorrectly when only `REDIS_URL` is set.

- [ ] **Step 3: Implement minimal compose/doc changes**
  Update exporter address interpolation and align docs/examples with the real precedence.

- [ ] **Step 4: Re-run render and diff checks**
  Run:
  `docker compose --env-file .env.production.example -f docker-compose.prod.yml -f docker-compose.monitoring.yml --profile monitoring config`
  `git diff --check -- docker-compose.monitoring.yml README.md .env.production.example`
  Expected: both pass.

### Task 3: Final Verification

**Files:**
- No new files expected

- [ ] **Step 1: Run backend verification**
  Run: `cd backend && GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./... -count=1`

- [ ] **Step 2: Run compose verification**
  Run: `docker compose --env-file .env.production.example -f docker-compose.prod.yml -f docker-compose.monitoring.yml --profile monitoring config >/tmp/compose.prod.monitoring.yaml`

- [ ] **Step 3: Summarize remaining risks**
  Note any still-best-effort behavior or operational prerequisites that remain.
