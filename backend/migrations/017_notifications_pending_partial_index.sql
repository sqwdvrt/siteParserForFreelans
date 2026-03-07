-- Narrow pending-only index for retry/digest paths.
-- Full (user_id, job_id) lookup is already covered by UNIQUE(user_id, job_id).
SELECT pg_advisory_lock(20260307, 17);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_pending_user_job
    ON notifications(user_id, job_id)
    WHERE status = 'pending';

SELECT pg_advisory_unlock(20260307, 17);
