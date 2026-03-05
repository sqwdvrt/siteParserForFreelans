-- Обратная связь пользователей на уведомления о проектах (👍/👎).
-- Идемпотентно: безопасно запускать повторно.

SELECT pg_advisory_lock(20260216, 1);

CREATE TABLE IF NOT EXISTS user_feedback (
    id         BIGSERIAL    PRIMARY KEY,
    user_id    BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id     BIGINT       NOT NULL REFERENCES jobs(id)  ON DELETE CASCADE,
    feedback   TEXT         NOT NULL CHECK (feedback IN ('good', 'bad')),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, job_id)
);

COMMENT ON TABLE  user_feedback           IS 'Feedback from users on job notifications (👍/👎).';
COMMENT ON COLUMN user_feedback.feedback  IS 'good = 👍, bad = 👎';

-- Быстрый поиск недавней обратной связи пользователя (AI matching threshold adjustment)
CREATE INDEX IF NOT EXISTS idx_user_feedback_user_created
    ON user_feedback (user_id, created_at DESC);

SELECT pg_advisory_unlock(20260216, 1);
