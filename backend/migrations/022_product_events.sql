-- Product analytics events for SQL dashboards in Grafana.

SELECT pg_advisory_lock(20260216, 22);

CREATE TABLE IF NOT EXISTS product_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    source VARCHAR(50),
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_events_created_at
    ON product_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_product_events_type_created_at
    ON product_events(event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_product_events_user_type_created_at
    ON product_events(user_id, event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_product_events_source_type_created_at
    ON product_events(source, event_type, created_at DESC);

SELECT pg_advisory_unlock(20260216, 22);
