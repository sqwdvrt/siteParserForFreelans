-- ANN Metadata Filtering: GIN indexes + denormalized profile columns
-- This enables efficient SQL-based filtering AFTER ANN candidate retrieval,
-- instead of loading ALL users into Python for pre-filtering.

-- 1. Denormalized structured profile columns (extracted from profile_text)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS work_type TEXT,
    ADD COLUMN IF NOT EXISTS experience_years FLOAT,
    ADD COLUMN IF NOT EXISTS desired_budget_min NUMERIC,
    ADD COLUMN IF NOT EXISTS stack TEXT[] NOT NULL DEFAULT '{}';

-- 2. GIN indexes for array-based filtering
CREATE INDEX IF NOT EXISTS idx_user_preferences_sources_gin
    ON user_preferences USING GIN (preferred_sources);

CREATE INDEX IF NOT EXISTS idx_user_preferences_include_gin
    ON user_preferences USING GIN (include_keywords);

CREATE INDEX IF NOT EXISTS idx_user_preferences_exclude_gin
    ON user_preferences USING GIN (exclude_keywords);

CREATE INDEX IF NOT EXISTS idx_users_stack_gin
    ON users USING GIN (stack);

-- 3. Partial index for matchable users (with embedding, not paused)
CREATE INDEX IF NOT EXISTS idx_users_matchable
    ON users (id, work_type, experience_years, stack, desired_budget_min, telegram_id)
    WHERE embedding IS NOT NULL;

-- 4. Trigger: auto-update denormalized columns when profile_text changes
-- This is a placeholder — actual extraction happens in Python at embedding time.
-- The trigger ensures new profile_text gets NULL columns until the AI service processes it.
CREATE OR REPLACE FUNCTION reset_structured_profile_on_profile_change()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.profile_text IS DISTINCT FROM OLD.profile_text THEN
        NEW.work_type := NULL;
        NEW.experience_years := NULL;
        NEW.desired_budget_min := NULL;
        NEW.stack := '{}';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reset_structured_profile ON users;
CREATE TRIGGER trg_reset_structured_profile
    BEFORE UPDATE OF profile_text ON users
    FOR EACH ROW
    EXECUTE FUNCTION reset_structured_profile_on_profile_change();
