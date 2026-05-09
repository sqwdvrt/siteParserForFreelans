CREATE TABLE IF NOT EXISTS user_profile_structured (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    stack TEXT[],
    specialization TEXT,
    level TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
