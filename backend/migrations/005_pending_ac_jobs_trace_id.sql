-- Persist trace_id for Actor-Critic pending matches to keep end-to-end tracing.
SELECT pg_advisory_lock(20260301, 5);

ALTER TABLE pending_ac_jobs
    ADD COLUMN IF NOT EXISTS trace_id TEXT;

SELECT pg_advisory_unlock(20260301, 5);
