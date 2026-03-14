-- Persist score decomposition for AC debugging.
SELECT pg_advisory_lock(20260312, 20);

ALTER TABLE pending_ac_jobs
    ADD COLUMN IF NOT EXISTS feedback_bonus REAL DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS preference_multiplier REAL DEFAULT 1.0;

SELECT pg_advisory_unlock(20260312, 20);
