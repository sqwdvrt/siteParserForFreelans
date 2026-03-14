-- Materialized user/job filter events for explainable matching stats.
SELECT pg_advisory_lock(20260312, 21);

CREATE TABLE IF NOT EXISTS user_job_filter_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 64),
    trace_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_job_filter_events_user_job_reason
    ON user_job_filter_events(user_id, job_id, reason);

CREATE INDEX IF NOT EXISTS idx_user_job_filter_events_user_reason_created
    ON user_job_filter_events(user_id, reason, created_at DESC);

SELECT pg_advisory_unlock(20260312, 21);

