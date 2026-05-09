-- Cross-platform job deduplication
-- When the same project appears on multiple freelance platforms (Kwork, FL.ru, etc.),
-- we track it as a single canonical job with duplicate_sources noting where else it was found.

-- Table to track duplicate relationships
CREATE TABLE IF NOT EXISTS job_duplicates (
    id              BIGSERIAL PRIMARY KEY,
    canonical_id    BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    duplicate_id    BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    similarity      FLOAT NOT NULL DEFAULT 0.0,
    found_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_different_jobs CHECK (canonical_id != duplicate_id)
);

-- Unique constraint: a job can only be a duplicate of one canonical job
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_duplicates_canonical
    ON job_duplicates(canonical_id, duplicate_id);

-- Index for fast lookup: "is this job a duplicate of anything?"
CREATE INDEX IF NOT EXISTS idx_job_duplicates_dup
    ON job_duplicates(duplicate_id);

-- Function: find potential duplicate job by title similarity
-- Uses trigram-like matching via simple string overlap on normalized titles
-- Returns the most similar active job from a DIFFERENT source
CREATE OR REPLACE FUNCTION find_similar_job(
    p_title TEXT,
    p_description TEXT,
    p_budget TEXT,
    p_exclude_source TEXT,
    p_similarity_threshold FLOAT DEFAULT 0.65
) RETURNS TABLE(job_id BIGINT, similarity_score FLOAT) AS $$
DECLARE
    norm_title TEXT;
    norm_search TEXT;
BEGIN
    -- Normalize title: lowercase, remove special chars
    norm_title := lower(regexp_replace(p_title, '[^a-zA-Zа-яА-ЯёЁ0-9\s]', '', 'g'));
    
    -- Simple word overlap similarity:
    -- Count matching words between titles / total unique words
    RETURN QUERY
    SELECT 
        j.id AS job_id,
        (
            -- Word overlap ratio: matching words / max(words_in_title1, words_in_title2)
            SELECT 
                COUNT(*)::FLOAT / 
                GREATEST(
                    cardinality(string_to_array(lower(regexp_replace(j.title, '[^a-zA-Zа-яА-ЯёЁ0-9\s]', '', 'g')), '\s+')),
                    cardinality(string_to_array(norm_title, '\s+'))
                )
            FROM unnest(string_to_array(norm_title, '\s+')) AS w(word)
            WHERE w.word != '' 
              AND w.word = ANY(string_to_array(lower(regexp_replace(j.title, '[^a-zA-Zа-яА-ЯёЁ0-9\s]', '', 'g')), '\s+'))
        ) AS similarity_score
    FROM jobs j
    WHERE j.status = 'active'
      AND j.source != p_exclude_source
      AND j.title IS NOT NULL
      AND LENGTH(j.title) > 3
      -- Fast pre-filter: title starts with same word or has similar length
      AND (
          lower(split_part(j.title, ' ', 1)) = lower(split_part(p_title, ' ', 1))
          OR ABS(LENGTH(j.title) - LENGTH(p_title)) < 50
      )
    HAVING (
        SELECT 
            COUNT(*)::FLOAT / 
            GREATEST(
                cardinality(string_to_array(lower(regexp_replace(j.title, '[^a-zA-Zа-яА-ЯёЁ0-9\s]', '', 'g')), '\s+')),
                cardinality(string_to_array(norm_title, '\s+'))
            )
        FROM unnest(string_to_array(norm_title, '\s+')) AS w(word)
        WHERE w.word != '' 
          AND w.word = ANY(string_to_array(lower(regexp_replace(j.title, '[^a-zA-Zа-яА-ЯёЁ0-9\s]', '', 'g')), '\s+'))
    ) >= p_similarity_threshold
    ORDER BY similarity_score DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;
