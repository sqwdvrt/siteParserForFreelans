-- Expiring Pro subscription window.
-- Existing Pro users get 30 days so current access does not disappear on deploy.

SELECT pg_advisory_lock(20260306, 32);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS pro_expires_at TIMESTAMPTZ;

UPDATE users
SET pro_expires_at = NOW() + INTERVAL '30 days'
WHERE is_pro = TRUE
  AND pro_expires_at IS NULL;

SELECT pg_advisory_unlock(20260306, 32);
