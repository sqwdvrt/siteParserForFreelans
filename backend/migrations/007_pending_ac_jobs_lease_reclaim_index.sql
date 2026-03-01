-- Speed up lease-based reclaim path for scheduler query:
-- ... WHERE processed_at IS NULL AND queued_at < NOW() - ...
-- Paired with idx_pending_ac_jobs_scheduleable (queued_at IS NULL) to optimize both OR branches.
SELECT pg_advisory_lock(20260301, 7);

CREATE INDEX IF NOT EXISTS idx_pending_ac_jobs_reclaimable
    ON pending_ac_jobs(user_id, queued_at, created_at)
    WHERE processed_at IS NULL AND queued_at IS NOT NULL;

SELECT pg_advisory_unlock(20260301, 7);
