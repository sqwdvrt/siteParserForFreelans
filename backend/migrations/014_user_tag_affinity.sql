-- Per-user affinity weights for classification tags/technologies.
SELECT pg_advisory_lock(20260306, 14);

CREATE TABLE IF NOT EXISTS user_tag_affinity (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    good_count INTEGER NOT NULL DEFAULT 0,
    bad_count INTEGER NOT NULL DEFAULT 0,
    weight REAL NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_user_tag_affinity_user_weight
    ON user_tag_affinity(user_id, weight DESC, updated_at DESC);

SELECT pg_advisory_unlock(20260306, 14);
