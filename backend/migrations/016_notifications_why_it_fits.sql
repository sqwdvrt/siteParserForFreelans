-- Add why_it_fits to notifications for pro digest.

SELECT pg_advisory_lock(20260306, 16);

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS why_it_fits TEXT;

SELECT pg_advisory_unlock(20260306, 16);
