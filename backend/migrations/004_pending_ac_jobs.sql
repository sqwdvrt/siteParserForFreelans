-- Actor-Critic pipeline: staging table for pending per-user matches.
-- Serialize migration across multiple containers (api/crawler/notifier).
SELECT pg_advisory_lock(20260228, 4);

CREATE TABLE IF NOT EXISTS pending_ac_jobs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    match_score REAL NOT NULL CHECK (match_score BETWEEN 0 AND 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_ac_jobs_user_job
    ON pending_ac_jobs(user_id, job_id);

CREATE INDEX IF NOT EXISTS idx_pending_ac_jobs_unprocessed_user_created
    ON pending_ac_jobs(user_id, created_at)
    WHERE processed_at IS NULL;

SELECT pg_advisory_unlock(20260228, 4);
