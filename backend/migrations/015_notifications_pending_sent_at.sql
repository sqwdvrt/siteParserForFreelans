-- pending notifications must not have sent_at until Telegram delivery succeeds.
SELECT pg_advisory_lock(20260307, 15);

ALTER TABLE notifications
    ALTER COLUMN sent_at DROP DEFAULT;

UPDATE notifications
SET sent_at = NULL
WHERE status = 'pending';

SELECT pg_advisory_unlock(20260307, 15);
