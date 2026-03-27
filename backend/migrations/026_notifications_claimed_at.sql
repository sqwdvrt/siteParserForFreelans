-- Track digest claim leases separately from sent_at to support stale sending reclaim.
SELECT pg_advisory_lock(20260327, 26);

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

COMMENT ON COLUMN notifications.claimed_at IS 'Lease timestamp for status=sending notifications.';

SELECT pg_advisory_unlock(20260327, 26);
