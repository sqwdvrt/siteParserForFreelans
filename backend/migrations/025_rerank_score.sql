-- Persist cross-encoder rerank score for pending Actor-only batches.
SELECT pg_advisory_lock(20260310, 25);

ALTER TABLE pending_ac_jobs
    ADD COLUMN IF NOT EXISTS rerank_score FLOAT DEFAULT 0.0;

SELECT pg_advisory_unlock(20260310, 25);
