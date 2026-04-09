# Spec: Matching Quality Upgrade

**Date:** 2026-04-09
**Status:** Approved

## Summary

Improve matching precision for freelancer profiles by tightening ANN and rerank thresholds, enriching user embeddings with structured profile signals, and exposing an admin-only `/debug_match` diagnostic flow that explains why a concrete project did or did not match a user.

## Goals

- Reduce obviously irrelevant projects reaching ANN/rerank.
- Preserve the existing queue topology and profile update flow.
- Add structured profile persistence without changing the core `users` and `jobs` schemas.
- Keep ML-specific computation in Python services, not in Go.
- Make matching diagnostics available to admins from Telegram without broadening public API access.

## Locked Product Decisions

- `SIMILARITY_THRESHOLD` default changes from `0.35` to `0.62`.
- `RERANK_THRESHOLD` default changes from `0.55` to `0.60`.
- Profile parsing is best-effort and must never block embedding generation.
- Parsed `stack` only backfills `user_preferences.include_keywords` when the field is currently empty.
- Parsed `min_budget_hint` only backfills `user_preferences.min_budget` when it is currently `NULL`.
- `/debug_match` is admin-only and available only to `ADMIN_TELEGRAM_ID`.
- External admin access stays on `backend-api`; the heavy ML/debug computation lives in `ai-service`.

## Architecture

### 1. Matching Thresholds

Environment defaults in `.env` and `.env.example` become stricter and gain inline rationale comments. `ai-service` consumers continue reading the same variables, so no queue or runtime contract changes are needed.

### 2. Structured Profile Parsing In User-Embed

`ProcessUserEmbedUseCase` becomes the single place that:

1. loads `users.profile_text`
2. optionally parses it with Gemini using a strict JSON prompt
3. persists structured fields into a new `user_profile_structured` table
4. backfills empty `user_preferences` fields from parsed data
5. builds expanded embedding text
6. generates and saves the embedding
7. enqueues rematch as today

Failures in steps 2-4 are soft failures: warn, skip enrichment, continue embedding.

### 3. Query Expansion

Embedding text is built from:

- parsed stack
- parsed specialization
- parsed level
- original profile text

The original profile text always remains present in the embedding payload so recall is not fully delegated to the parser.

### 4. Debug Match Flow

`backend-api` exposes:

- `GET /admin/debug/match?user_id={id}&job_url={url}`

This endpoint:

- validates admin Bearer token
- validates `user_id` and `job_url`
- calls an internal `ai-service` debug endpoint
- returns the debug payload as-is

`ai-service` exposes an internal debug endpoint that:

- loads the user, user embedding, job, job embedding, and user preferences
- computes embedding similarity
- computes rerank score using the existing cross-encoder
- evaluates `preference_filter`
- derives final pass/fail reasoning against current thresholds

`telegram-bot` adds `/debug_match <job_url>` as an admin-only command and formats the debug payload into a readable Telegram message.

## Data Model

New table:

```sql
CREATE TABLE user_profile_structured (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    stack TEXT[],
    specialization TEXT,
    level TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

The table is owned by the user-embed path. It is not a public API surface in this change.

## API Contracts

### Internal ai-service debug endpoint

Request:

- `user_id`
- `job_url`

Response fields:

- project metadata: title, source, url
- `embedding_similarity`
- `similarity_threshold`
- `rerank_score`
- `rerank_threshold`
- `preference_filter_passed`
- `preference_filter_reason`
- `final_score`
- extracted project stack
- parsed profile stack
- stack intersection
- human-readable conclusion

### External backend admin endpoint

The backend response mirrors the internal ai-service response and stays behind `ADMIN_AUTH_TOKEN`.

## Error Handling

- Gemini timeout / HTTP failure / JSON parse failure:
  - log warning
  - skip structured persistence/backfill
  - continue embedding from original profile text
- Missing user/job embedding in debug endpoint:
  - return explanatory payload rather than generic 500 when possible
- Missing reranker:
  - return `rerank_score = null` and explain that reranker is unavailable
- Invalid bot caller for `/debug_match`:
  - Telegram bot returns a generic forbidden/admin-only message

## Testing Strategy

- Python unit tests for structured parse fallback, preference backfill, and query expansion.
- Python unit tests for internal ai-service debug calculation path.
- Go handler/repository tests for `/admin/debug/match` proxying and auth.
- Telegram bot tests for admin gating and output formatting.
- Targeted config tests for new env defaults and comments where existing test coverage exists.

## Out Of Scope

- No change to Redis queue names or payload formats.
- No redesign of `jobs`, `users`, or `notifications` tables.
- No public UI for editing `user_profile_structured`.
- No migration of existing profiles in batch; enrichment happens lazily on future `user-embed`.
