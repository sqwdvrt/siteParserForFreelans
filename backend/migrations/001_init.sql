-- Site Parser for Freelance: Initial schema
-- Idempotent: safe to run multiple times

CREATE EXTENSION IF NOT EXISTS vector;

-- Проекты (raw + extracted)
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL DEFAULT 'kwork',
    url VARCHAR(500) NOT NULL,
    external_id VARCHAR(100),
    title TEXT NOT NULL,
    description TEXT,
    budget VARCHAR(100),
    skills TEXT[],
    posted_at TIMESTAMPTZ,
    raw_html TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url_unique ON jobs(url);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at DESC);

-- Embeddings
CREATE TABLE IF NOT EXISTS job_embeddings (
    job_id BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    embedding vector(384),
    ai_metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW: работает на пустой таблице (ivfflat требует данных)
CREATE INDEX IF NOT EXISTS idx_job_embeddings_vector ON job_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- Пользователи
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    profile_text TEXT,
    embedding vector(384),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);

-- История уведомлений (дедупликация + аналитика)
CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    job_id BIGINT REFERENCES jobs(id),
    match_score FLOAT,
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, job_id)
);
