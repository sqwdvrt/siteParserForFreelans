-- Add queued_at lease column to prevent duplicate scheduling.
-- Rows with queued_at IS NOT NULL (and within lease window) are skipped by the scheduler.
SELECT pg_advisory_lock(20260301, 6);

ALTER TABLE pending_ac_jobs
    ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ;

-- Scheduler uses this index to find rows eligible for enqueueing.
CREATE INDEX IF NOT EXISTS idx_pending_ac_jobs_scheduleable
    ON pending_ac_jobs(user_id, created_at)
    WHERE processed_at IS NULL AND queued_at IS NULL;

SELECT pg_advisory_unlock(20260301, 6);
