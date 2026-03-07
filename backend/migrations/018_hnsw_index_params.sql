-- Pin explicit HNSW build params instead of relying on pgvector defaults.
SELECT pg_advisory_lock(20260307, 18);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_job_embeddings_vector_hnsw_m16_ef64
    ON job_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_embedding_vector_hnsw_m16_ef64
    ON users
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

DROP INDEX CONCURRENTLY IF EXISTS idx_job_embeddings_vector;
DROP INDEX CONCURRENTLY IF EXISTS idx_users_embedding_vector;

SELECT pg_advisory_unlock(20260307, 18);
