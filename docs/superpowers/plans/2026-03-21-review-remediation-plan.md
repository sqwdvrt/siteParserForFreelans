# Review Findings Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five current review findings without introducing new behavior drift in crawler, notifier, browser-service, AI consumer, or Docker Compose.

**Architecture:** Fix the issues in dependency order: first restore security and reachability invariants in `browser-service` and crawler expiry, then repair Redis poison-message handling, then remove AI-service behavior drift and import coupling, and finally restore environment isolation in Compose. Each task keeps the public contract as stable as possible and adds regression tests before implementation.

**Tech Stack:** Go, Python, FastAPI/Playwright, Redis, PostgreSQL, Docker Compose, pytest, Go test

---

### Task 1: Harden browser-service SSRF protection against rebinding and event-loop blocking

**Files:**
- Modify: `browser-service/main.py`
- Modify: `browser-service/tests/test_main.py`

- [ ] **Step 1: Write failing tests for the actual threat model**
  Add tests for:
  - validation failure when DNS resolution fails
  - rebinding/TOCTOU-safe path that does not rely on a second independent resolver during fetch
  - async-safe host resolution path rather than direct blocking `socket.getaddrinfo()` in the request handler

- [ ] **Step 2: Run targeted tests to verify the new cases fail**
  Run: `cd browser-service && ./.venv/bin/python -m pytest -q tests/test_main.py`
  Expected: new tests fail before implementation

- [ ] **Step 3: Replace preflight-only DNS validation with fetch-time enforcement**
  Implement one of these patterns, preferring the first:
  - route all network requests through a Playwright request interceptor that resolves hostnames once and blocks non-public targets at request time
  - or perform async DNS resolution and force navigation through a vetted resolved endpoint while preserving the original `Host` header only if Playwright/browser constraints allow it safely

- [ ] **Step 4: Move DNS lookup off the event loop**
  Replace direct synchronous `socket.getaddrinfo()` in the async request path with an async wrapper such as `asyncio.to_thread(...)` or equivalent.

- [ ] **Step 5: Re-run browser-service tests**
  Run: `cd browser-service && ./.venv/bin/python -m pytest -q tests/test_main.py`
  Expected: all browser-service tests pass

- [ ] **Step 6: Commit**
  Run:
  ```bash
  git add browser-service/main.py browser-service/tests/test_main.py
  git commit -m "fix: harden browser-service SSRF validation"
  ```

### Task 2: Make immediate expiry on 404/410 reachable and testable

**Files:**
- Modify: `backend/internal/usecase/crawl_projects.go`
- Modify: `backend/internal/usecase/crawl_projects_test.go`
- Modify: `backend/internal/adapter/postgres/repository.go`
- Modify: `backend/internal/port/repository.go` only if interface changes are still required

- [ ] **Step 1: Write a failing crawler test for active jobs that now return 404/410**
  Add a test proving an already-known active job can be expired immediately when the detail URL becomes gone.

- [ ] **Step 2: Run targeted crawler tests**
  Run: `cd backend && GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/usecase -run TestCrawlProjects -count=1`
  Expected: the new 404/410 case fails before the code change

- [ ] **Step 3: Fix the control flow**
  Refactor crawler logic so the 404/410 branch is reachable for previously active jobs. Likely options:
  - keep fetching details for known URLs on a schedule and expire on `IsGone(err)`
  - or teach `ExistsByURL`/crawler state handling to distinguish “known active” from “skip fetch”
  Choose the smallest change that preserves current crawl volume expectations.

- [ ] **Step 4: Keep repository semantics coherent**
  Ensure `ExpireByURL`, `ExistsByURL`, and `TouchSeenAt` still form a consistent state machine for `active`/`expired` jobs.

- [ ] **Step 5: Re-run crawler/usecase tests**
  Run:
  ```bash
  cd backend
  GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/usecase ./cmd/crawler -count=1
  ```
  Expected: pass

- [ ] **Step 6: Commit**
  Run:
  ```bash
  git add backend/internal/usecase/crawl_projects.go backend/internal/usecase/crawl_projects_test.go backend/internal/adapter/postgres/repository.go backend/internal/port/repository.go
  git commit -m "fix: make crawler expire gone jobs correctly"
  ```

### Task 3: Repair match-notify poison-message handling

**Files:**
- Modify: `backend/internal/adapter/redis/match_notify_consumer.go`
- Modify: `backend/internal/adapter/redis/match_notify_consumer_test.go`
- Review only: `backend/cmd/notifier/main.go`

- [ ] **Step 1: Add failing regression tests**
  Add tests for:
  - DLQ move failure returns an error instead of silently leaving the message in `:processing`
  - successful JSON poison routing does not trigger the notifier’s infrastructure-failure semantics unnecessarily
  - `:processing` is drained on successful DLQ routing

- [ ] **Step 2: Run targeted Redis/notifier tests**
  Run:
  ```bash
  cd backend
  GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/adapter/redis ./cmd/notifier -count=1
  ```
  Expected: new cases fail before implementation

- [ ] **Step 3: Centralize requeue/DLQ move semantics**
  Introduce a small helper around `requeueScript.Run(...).Int()` that:
  - returns an error on Redis/Lua failure
  - returns an error when the script reports `0`
  - is reused by DLQ, `Nack`, and `Requeue`

- [ ] **Step 4: Normalize poison-message behavior**
  After a successful move to DLQ:
  - invalid JSON should not masquerade as infrastructure failure if the message is already quarantined
  - invalid structured payload should remain a soft skip

- [ ] **Step 5: Re-run Redis/notifier tests**
  Run:
  ```bash
  cd backend
  GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/adapter/redis ./cmd/notifier -count=1
  ```
  Expected: pass

- [ ] **Step 6: Commit**
  Run:
  ```bash
  git add backend/internal/adapter/redis/match_notify_consumer.go backend/internal/adapter/redis/match_notify_consumer_test.go backend/cmd/notifier/main.go
  git commit -m "fix: harden notifier poison message handling"
  ```

### Task 4: Remove AI consumer behavior drift and import coupling

**Files:**
- Modify: `ai-service/cmd/consumer/main.py`
- Modify: `ai-service/tests/` relevant startup tests
- Modify: `ai-service/src/ai_service/adapter/postgres/repository.py`
- Modify: `ai-service/src/ai_service/adapter/postgres/match_repository.py`
- Create or modify: a low-level constants module for embedding metadata, for example `ai-service/src/ai_service/domain/embedding.py` or another neutral location already used by the project

- [ ] **Step 1: Add failing tests for explicit opt-in**
  Add a startup/config test proving the plain consumer does not enable Gemini classification merely because `GEMINI_API_KEY` exists.

- [ ] **Step 2: Add a failing import-boundary regression test if practical**
  At minimum, document via test or isolated import path that Postgres repositories do not require `sentence_transformers` to import.

- [ ] **Step 3: Re-run targeted AI tests**
  Run:
  ```bash
  cd ai-service && ../scripts/pytest_ai.sh -q tests/test_preference_filter.py tests/test_runtime_env.py tests/test_ac_consumer_main.py
  ```
  Expected: new explicit-opt-in/import tests fail first

- [ ] **Step 4: Restore explicit classifier opt-in**
  Pick one explicit control:
  - dedicated env like `ENABLE_GEMINI_CLASSIFIER=1`
  - or existing provider-based config if one already fits this path
  Keep default behavior identical to pre-diff: no classifier unless explicitly enabled.

- [ ] **Step 5: Decouple `EMBEDDING_DIM` from the sentence-transformers adapter**
  Move the dimension constant to a neutral module that can be imported by repositories without pulling in Torch/model code.

- [ ] **Step 6: Re-run targeted AI tests**
  Run:
  ```bash
  cd ai-service && ../scripts/pytest_ai.sh -q tests/test_preference_filter.py tests/test_runtime_env.py tests/test_ac_consumer_main.py
  ```
  Expected: pass

- [ ] **Step 7: Commit**
  Run:
  ```bash
  git add ai-service/cmd/consumer/main.py ai-service/src/ai_service/adapter/postgres/repository.py ai-service/src/ai_service/adapter/postgres/match_repository.py ai-service/src/ai_service/tests
  git commit -m "fix: restore explicit ai consumer classifier config"
  ```

### Task 5: Restore Compose network isolation and keep monitoring connectivity

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.prod.yml`
- Modify: `docker-compose.monitoring.yml`
- Modify: `README.md` if startup commands or assumptions change

- [ ] **Step 1: Decide the network strategy**
  Preferred approach:
  - restore Compose-managed per-project default networks in dev/prod
  - if monitoring must join the app network, make that network name configurable instead of hard-coded

- [ ] **Step 2: Add or update documentation/tests for the chosen network contract**
  At minimum, update startup instructions so monitoring attach behavior is explicit.

- [ ] **Step 3: Implement the network change**
  Remove unconditional shared network naming unless there is a clear operational requirement. If an external network is still needed for monitoring, gate it behind a variable such as `COMPOSE_PROJECT_NETWORK` or document a one-time create/join flow.

- [ ] **Step 4: Validate Compose configs**
  Run:
  ```bash
  docker compose -f docker-compose.yml config >/tmp/compose.dev.yaml
  docker compose -f docker-compose.prod.yml -f docker-compose.monitoring.yml config >/tmp/compose.prod.monitoring.yaml
  ```
  Expected: both configs render successfully

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add docker-compose.yml docker-compose.prod.yml docker-compose.monitoring.yml README.md
  git commit -m "fix: restore compose network isolation"
  ```

### Task 6: Final verification sweep

**Files:**
- No new files expected

- [ ] **Step 1: Run backend verification**
  Run:
  ```bash
  cd backend
  GOTOOLCHAIN=local GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/usecase ./internal/adapter/redis ./cmd/crawler ./cmd/notifier -count=1
  ```

- [ ] **Step 2: Run AI verification**
  Run:
  ```bash
  cd ai-service && ../scripts/pytest_ai.sh -q tests/test_preference_filter.py tests/test_runtime_env.py tests/test_ac_consumer_main.py
  ```

- [ ] **Step 3: Run browser-service verification**
  Run:
  ```bash
  cd browser-service && ./.venv/bin/python -m pytest -q tests/test_main.py
  ```

- [ ] **Step 4: Render Compose configs one more time**
  Run:
  ```bash
  docker compose -f docker-compose.yml config >/tmp/compose.dev.yaml
  docker compose -f docker-compose.prod.yml -f docker-compose.monitoring.yml config >/tmp/compose.prod.monitoring.yaml
  ```

- [ ] **Step 5: Prepare review handoff**
  Summarize:
  - what changed
  - which review findings are closed
  - which tradeoffs remain, if any
