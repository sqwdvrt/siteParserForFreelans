-- Jobs freshness: status + last_seen_at для экспирации закрытых проектов.
-- status: 'active' | 'expired'
-- last_seen_at: обновляется краулером при каждом обходе (TouchSeenAt).
SELECT pg_advisory_lock(20260314, 24);

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS status       TEXT        NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Используется ExpireStaleJobs (WHERE status='active' AND last_seen_at < threshold)
-- и фильтром матчинга (WHERE status='active').
CREATE INDEX IF NOT EXISTS idx_jobs_status_seen
    ON jobs(status, last_seen_at DESC);

SELECT pg_advisory_unlock(20260314, 24);
