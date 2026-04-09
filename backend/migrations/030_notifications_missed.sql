-- Add partial index for missed notifications (found but not sent due to limits).
-- Enables efficient top-N queries for daily rollover.
SELECT pg_advisory_lock(20260327, 30);

CREATE INDEX IF NOT EXISTS idx_notifications_missed_top_score
    ON notifications(user_id, COALESCE(final_score, match_score, 0) DESC)
    WHERE status = 'missed';

COMMENT ON INDEX idx_notifications_missed_top_score IS 'Top-N missed notifications per user for rollover conversion';

SELECT pg_advisory_unlock(20260327, 30);
