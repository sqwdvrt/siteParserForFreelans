ALTER TABLE users
    ADD COLUMN IF NOT EXISTS paused_until timestamptz;
