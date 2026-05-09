-- Legacy notifier index without status causes broader scans than the
-- status-aware replacement from 008_notifications_status_index.sql.
-- Keep only the status-aware index for sent/rate-limit queries.
SELECT pg_advisory_lock(20260314, 23);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_user_status_sent
    ON notifications(user_id, status, sent_at DESC);

DROP INDEX CONCURRENTLY IF EXISTS idx_notifications_user_sent;

SELECT pg_advisory_unlock(20260314, 23);
