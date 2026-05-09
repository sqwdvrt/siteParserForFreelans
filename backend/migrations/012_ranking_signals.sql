-- Ranking signals and audit fields for v2 ranker.
SELECT pg_advisory_lock(20260306, 12);

ALTER TABLE pending_ac_jobs
    ADD COLUMN IF NOT EXISTS raw_similarity REAL,
    ADD COLUMN IF NOT EXISTS final_score REAL,
    ADD COLUMN IF NOT EXISTS ranker_version TEXT,
    ADD COLUMN IF NOT EXISTS reason_codes TEXT[];

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS ranker_version TEXT,
    ADD COLUMN IF NOT EXISTS final_score REAL,
    ADD COLUMN IF NOT EXISTS reason_codes TEXT[];

SELECT pg_advisory_unlock(20260306, 12);
