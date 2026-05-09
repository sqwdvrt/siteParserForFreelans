SELECT pg_advisory_lock(20260301, 8);

-- Composite index covering queries that filter by (user_id, status) and order/filter by sent_at:
--   SentRecently:  WHERE user_id = $1 AND status = 'sent' AND sent_at > NOW() - interval
--   CountToday:    WHERE user_id = $1 AND status = 'sent' AND sent_at >= date_trunc(...)
--   EnsurePending: WHERE user_id = $1 AND status = 'pending'
-- Replaces the partial coverage of idx_notifications_user_sent (user_id, sent_at DESC).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notifications_user_status_sent
    ON notifications(user_id, status, sent_at DESC);

SELECT pg_advisory_unlock(20260301, 8);
