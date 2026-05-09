-- Pro subscription flags and digest hour in users table.
-- Managed via DB/admin tooling (no payment gateway coupling in backend).

SELECT pg_advisory_lock(20260306, 11);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_pro BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS notify_hour SMALLINT;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'users_notify_hour_range'
  ) THEN
    ALTER TABLE users
      ADD CONSTRAINT users_notify_hour_range
      CHECK (notify_hour IS NULL OR (notify_hour >= 0 AND notify_hour <= 23));
  END IF;
END $$;

SELECT pg_advisory_unlock(20260306, 11);
