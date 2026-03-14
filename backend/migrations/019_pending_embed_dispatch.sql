-- Transactional staging/outbox tables for deferred Redis dispatch of user-embed and ai-process jobs.
SELECT pg_advisory_lock(20260310, 19);

CREATE TABLE IF NOT EXISTS pending_user_embeds (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    trace_id TEXT,
    traceparent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    queued_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pending_user_embeds_dispatchable
    ON pending_user_embeds (created_at)
    WHERE queued_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pending_user_embeds_reclaimable
    ON pending_user_embeds (queued_at, created_at)
    WHERE queued_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS pending_job_embeds (
    job_id BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    trace_id TEXT,
    traceparent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    queued_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pending_job_embeds_dispatchable
    ON pending_job_embeds (created_at)
    WHERE queued_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pending_job_embeds_reclaimable
    ON pending_job_embeds (queued_at, created_at)
    WHERE queued_at IS NOT NULL;

SELECT pg_advisory_unlock(20260310, 19);
