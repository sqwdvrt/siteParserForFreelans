-- User-level matching preferences for personalized ranking/filtering.
SELECT pg_advisory_lock(20260306, 13);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    include_keywords TEXT[] NOT NULL DEFAULT '{}',
    exclude_keywords TEXT[] NOT NULL DEFAULT '{}',
    min_budget NUMERIC,
    max_budget NUMERIC,
    preferred_sources TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT user_preferences_budget_range
        CHECK (min_budget IS NULL OR max_budget IS NULL OR min_budget <= max_budget)
);

ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE tablename = 'user_preferences'
      AND policyname = 'user_preferences_isolate'
  ) THEN
    CREATE POLICY user_preferences_isolate
    ON user_preferences FOR ALL
    USING (
      COALESCE(NULLIF(trim(current_setting('app.current_user_id', true)), ''), '') = ''
      OR user_id = NULLIF(trim(current_setting('app.current_user_id', true)), '')::bigint
    )
    WITH CHECK (
      COALESCE(NULLIF(trim(current_setting('app.current_user_id', true)), ''), '') = ''
      OR user_id = NULLIF(trim(current_setting('app.current_user_id', true)), '')::bigint
    );
  END IF;
END $$;

SELECT pg_advisory_unlock(20260306, 13);
