-- Matching optimization: accelerate anti-join subquery
-- WHERE u.id NOT IN (SELECT user_id FROM notifications WHERE job_id = $1)

-- Serialize migration across multiple containers (api/crawler/notifier).
SELECT pg_advisory_lock(20260222, 2);

CREATE INDEX IF NOT EXISTS idx_notifications_job_user
    ON notifications(job_id, user_id);

SELECT pg_advisory_unlock(20260222, 2);
