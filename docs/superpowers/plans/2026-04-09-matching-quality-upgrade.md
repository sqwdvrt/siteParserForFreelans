# Matching Quality Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten matching quality by raising thresholds, enriching user embeddings with structured profile data, and adding an admin-only `/debug_match` flow.

**Architecture:** Keep ML-sensitive parsing, similarity, rerank, and preference-filter logic in `ai-service`. `backend-api` only owns authentication and HTTP proxying for admin debug access. `telegram-bot` remains a thin admin client. Structured profile enrichment is lazy and happens during normal `user-embed` processing.

**Tech Stack:** Python (`ai-service`, `telegram-bot`), Go (`backend-api`), PostgreSQL, Gemini API, sentence-transformers, pytest, Go test.

---

## File Map

**Config**

- Modify: `.env`
- Modify: `.env.example`

**Database**

- Create: `backend/migrations/031_user_profile_structured.sql`
- Modify: `backend/migrations/manifest.txt`

**ai-service**

- Modify: `ai-service/cmd/user_embed_consumer/main.py`
- Create: `ai-service/src/ai_service/port/profile_parser.py`
- Create: `ai-service/src/ai_service/adapter/gemini/profile_parser.py`
- Modify: `ai-service/src/ai_service/adapter/gemini/__init__.py`
- Modify: `ai-service/src/ai_service/port/user_repository.py`
- Modify: `ai-service/src/ai_service/adapter/postgres/user_repository.py`
- Modify: `ai-service/src/ai_service/domain/user.py`
- Modify: `ai-service/src/ai_service/usecase/process_user_embed.py`
- Create: `ai-service/src/ai_service/usecase/debug_match.py`
- Create: `ai-service/src/ai_service/util/profile_embedding_text.py`
- Create: `ai-service/src/ai_service/util/debug_http_server.py`
- Modify: `ai-service/cmd/consumer/main.py`
- Test: `ai-service/tests/test_process_user_embed.py`
- Test: `ai-service/tests/test_user_embed_consumer_main.py`
- Create: `ai-service/tests/test_gemini_profile_parser.py`
- Create: `ai-service/tests/test_debug_match.py`

**backend**

- Modify: `backend/internal/port/admin_repository.go`
- Modify: `backend/internal/api/admin_handlers.go`
- Modify: `backend/internal/api/admin_handlers_test.go`
- Modify: `backend/cmd/api/main.go`

**telegram-bot**

- Create: `telegram-bot/handlers/debug.py`
- Modify: `telegram-bot/main.py`
- Test: `telegram-bot/tests/test_api_helpers.py`
- Test: `telegram-bot/tests/test_polling.py`

### Task 1: Raise Matching Threshold Defaults

**Files:**
- Modify: `.env`
- Modify: `.env.example`

- [ ] **Step 1: Write the failing config assertions**

Add or update a lightweight test if one exists for env defaults; otherwise prepare an exact grep verification for:
- `SIMILARITY_THRESHOLD=0.62`
- `RERANK_THRESHOLD=0.60`
- new comments
- `PROFILE_PARSE_ENABLED=true`
- `ADMIN_TELEGRAM_ID=`

- [ ] **Step 2: Run the verification to observe the old defaults**

Run:

```bash
rg -n "SIMILARITY_THRESHOLD|RERANK_THRESHOLD|PROFILE_PARSE_ENABLED|ADMIN_TELEGRAM_ID" .env .env.example
```

Expected: old `0.35` / `0.55`, missing new variables.

- [ ] **Step 3: Update defaults and comments**

Change `.env` and `.env.example` to the new thresholds and add comments exactly near the variables.

- [ ] **Step 4: Re-run verification**

Run the same `rg` command and confirm the new values are present.

- [ ] **Step 5: Commit**

```bash
git add .env .env.example
git commit -m "Raise default matching thresholds"
```

### Task 2: Add Structured Profile Schema

**Files:**
- Create: `backend/migrations/031_user_profile_structured.sql`
- Modify: `backend/migrations/manifest.txt`

- [ ] **Step 1: Write the migration**

Create the table:

```sql
CREATE TABLE user_profile_structured (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    stack TEXT[],
    specialization TEXT,
    level TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

Use UPSERT-friendly semantics by planning repository writes around `ON CONFLICT (user_id)`.

- [ ] **Step 2: Register the migration**

Append the new migration file to `backend/migrations/manifest.txt` without disturbing existing entries.

- [ ] **Step 3: Verify migration registration**

Run:

```bash
tail -n 5 backend/migrations/manifest.txt
```

Expected: includes `031_user_profile_structured.sql`.

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/031_user_profile_structured.sql backend/migrations/manifest.txt
git commit -m "Add structured user profile table"
```

### Task 3: Add Best-Effort Gemini Profile Parsing And Query Expansion

**Files:**
- Create: `ai-service/src/ai_service/port/profile_parser.py`
- Create: `ai-service/src/ai_service/adapter/gemini/profile_parser.py`
- Modify: `ai-service/src/ai_service/adapter/gemini/__init__.py`
- Modify: `ai-service/src/ai_service/domain/user.py`
- Modify: `ai-service/src/ai_service/port/user_repository.py`
- Modify: `ai-service/src/ai_service/adapter/postgres/user_repository.py`
- Create: `ai-service/src/ai_service/util/profile_embedding_text.py`
- Modify: `ai-service/src/ai_service/usecase/process_user_embed.py`
- Modify: `ai-service/cmd/user_embed_consumer/main.py`
- Test: `ai-service/tests/test_process_user_embed.py`
- Test: `ai-service/tests/test_gemini_profile_parser.py`
- Test: `ai-service/tests/test_user_embed_consumer_main.py`

- [ ] **Step 1: Write failing tests for process-user-embed enrichment**

Cover:
- parse failures do not block embedding save
- include keywords are backfilled only when currently empty
- min budget is backfilled only when currently `None`
- structured profile row is saved
- expanded embedding text includes stack/specialization/level + original profile text

- [ ] **Step 2: Run the targeted tests to verify failure**

Run:

```bash
cd ai-service && pytest -q tests/test_process_user_embed.py tests/test_user_embed_consumer_main.py -k "embed or profile"
```

Expected: FAIL because parser integration and repository methods do not exist yet.

- [ ] **Step 3: Add the parser contract and Gemini implementation**

Implement a dedicated profile parser around the exact prompt from the task.

Behavior:
- strict JSON request
- returns normalized dict / dataclass
- catches timeout, HTTP, and JSON errors
- logs warning and returns `None`

- [ ] **Step 4: Add repository methods for structured persistence and conditional preference backfill**

Extend the user repository with focused methods:
- upsert structured profile
- backfill include keywords only when empty
- backfill min budget only when null

Do not overload `save_embedding`.

- [ ] **Step 5: Add `build_embedding_text()` helper and integrate it**

Move query-expansion formatting to a dedicated utility so the same logic can be reused in tests and debug flows.

- [ ] **Step 6: Wire parser creation in `user_embed_consumer`**

Create the Gemini parser only when:
- `PROFILE_PARSE_ENABLED=true`
- `GEMINI_API_KEY` is present

Otherwise continue with embedding-only behavior.

- [ ] **Step 7: Re-run targeted ai-service tests**

Run:

```bash
cd ai-service && pytest -q tests/test_process_user_embed.py tests/test_user_embed_consumer_main.py tests/test_gemini_profile_parser.py
```

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add ai-service/cmd/user_embed_consumer/main.py ai-service/src/ai_service/port/profile_parser.py ai-service/src/ai_service/adapter/gemini/profile_parser.py ai-service/src/ai_service/adapter/gemini/__init__.py ai-service/src/ai_service/domain/user.py ai-service/src/ai_service/port/user_repository.py ai-service/src/ai_service/adapter/postgres/user_repository.py ai-service/src/ai_service/util/profile_embedding_text.py ai-service/src/ai_service/usecase/process_user_embed.py ai-service/tests/test_process_user_embed.py ai-service/tests/test_gemini_profile_parser.py ai-service/tests/test_user_embed_consumer_main.py
git commit -m "Enrich user embeddings with structured profiles"
```

### Task 4: Add ai-service Debug Match Endpoint

**Files:**
- Create: `ai-service/src/ai_service/usecase/debug_match.py`
- Create: `ai-service/src/ai_service/util/debug_http_server.py`
- Modify: `ai-service/src/ai_service/adapter/postgres/user_repository.py`
- Modify: `ai-service/cmd/consumer/main.py`
- Test: `ai-service/tests/test_debug_match.py`

- [ ] **Step 1: Write failing tests for debug-match metrics**

Cover:
- similarity is computed from existing embeddings
- rerank score uses existing cross-encoder when available
- preference filter result and reason are included
- conclusion text reflects threshold failures correctly

- [ ] **Step 2: Run targeted tests to verify failure**

Run:

```bash
cd ai-service && pytest -q tests/test_debug_match.py
```

Expected: FAIL because the use case and HTTP server do not exist.

- [ ] **Step 3: Implement the pure debug use case**

Keep the computation separate from HTTP:
- load user/job
- compute similarity
- compute rerank score
- evaluate preference filter
- compute stack intersection and human-readable conclusion

- [ ] **Step 4: Expose a minimal internal HTTP endpoint**

Add a tiny authenticated-by-network-only endpoint for backend-to-ai calls. Use a separate port env to avoid colliding with metrics/health.

- [ ] **Step 5: Wire the endpoint from the consumer runtime**

Start the internal debug server in `ai-service/cmd/consumer/main.py` only when enabled by env.

- [ ] **Step 6: Re-run ai-service tests**

Run:

```bash
cd ai-service && pytest -q tests/test_debug_match.py tests/test_consumer_main.py
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add ai-service/src/ai_service/usecase/debug_match.py ai-service/src/ai_service/util/debug_http_server.py ai-service/cmd/consumer/main.py ai-service/tests/test_debug_match.py
git commit -m "Add ai-service debug match endpoint"
```

### Task 5: Add backend Admin Debug Proxy

**Files:**
- Modify: `backend/internal/api/admin_handlers.go`
- Modify: `backend/internal/api/admin_handlers_test.go`
- Modify: `backend/cmd/api/main.go`

- [ ] **Step 1: Write failing handler tests**

Cover:
- admin auth still required
- missing `user_id` / `job_url` returns `400`
- successful proxy returns JSON payload from ai-service
- upstream failure returns `502` or `500` consistently

- [ ] **Step 2: Run targeted tests**

Run:

```bash
cd backend && GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/api -count=1
```

Expected: FAIL because the handler and wiring do not exist.

- [ ] **Step 3: Add a small debug client in the admin handlers layer**

Do not extend the admin repository for ML computation. Keep this as HTTP proxy logic inside admin handler dependencies.

- [ ] **Step 4: Wire endpoint in API main**

Add env for the internal ai-service debug base URL and register:

```text
GET /admin/debug/match
```

- [ ] **Step 5: Re-run backend API tests**

Run the same `go test` command and confirm PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/internal/api/admin_handlers.go backend/internal/api/admin_handlers_test.go backend/cmd/api/main.go
git commit -m "Add admin debug match proxy"
```

### Task 6: Add Telegram `/debug_match`

**Files:**
- Create: `telegram-bot/handlers/debug.py`
- Modify: `telegram-bot/main.py`
- Modify: `telegram-bot/tests/test_api_helpers.py`
- Modify: `telegram-bot/tests/test_polling.py`

- [ ] **Step 1: Write failing bot tests**

Cover:
- non-admin users cannot call `/debug_match`
- admin users without URL get usage hint
- admin users with URL format response correctly when API returns metrics

- [ ] **Step 2: Run targeted tests**

Run:

```bash
cd telegram-bot && pytest -q tests/test_api_helpers.py tests/test_polling.py -k "debug_match or admin"
```

Expected: FAIL because command and helper do not exist.

- [ ] **Step 3: Add debug helper module**

Create `telegram-bot/handlers/debug.py` for:
- admin check
- backend admin request helper
- response formatting helper

Keep `main.py` as thin as possible and call into the module.

- [ ] **Step 4: Register the command in bot routing**

Add `/debug_match <job_url>` handling without exposing it in the public command list.

- [ ] **Step 5: Re-run bot tests**

Run:

```bash
cd telegram-bot && pytest -q tests/test_api_helpers.py tests/test_polling.py
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add telegram-bot/handlers/debug.py telegram-bot/main.py telegram-bot/tests/test_api_helpers.py telegram-bot/tests/test_polling.py
git commit -m "Add telegram debug match command"
```

### Task 7: Run Final Verification

**Files:**
- No new files

- [ ] **Step 1: Run ai-service verification**

```bash
cd ai-service && pytest -q
```

- [ ] **Step 2: Run backend verification**

```bash
cd backend && GOMODCACHE=$(pwd)/.gomodcache GOCACHE=$(pwd)/.gocache go test ./internal/api ./internal/adapter/postgres -count=1
```

- [ ] **Step 3: Run telegram-bot verification**

```bash
cd telegram-bot && ruff check main.py handlers tests && pytest -q
```

- [ ] **Step 4: Summarize residual risks**

Call out:
- debug endpoint operational env wiring
- migration application requirement before deploy
- parser is best-effort and will silently degrade to plain embedding when Gemini is unavailable
