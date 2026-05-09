package postgres

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// FeedbackRepository реализует port.FeedbackRepository через pgx.
type FeedbackRepository struct {
	pool *pgxpool.Pool
}

// NewFeedbackRepository создаёт репозиторий обратной связи.
func NewFeedbackRepository(pool *pgxpool.Pool) *FeedbackRepository {
	return &FeedbackRepository{pool: pool}
}

// Upsert вставляет или обновляет feedback для (user_id, job_id).
func (r *FeedbackRepository) Upsert(ctx context.Context, userID, jobID int64, fb domain.FeedbackType) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin feedback tx: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	const q = `
		INSERT INTO user_feedback (user_id, job_id, feedback)
		SELECT $1, $2, $3
		WHERE EXISTS (
			SELECT 1
			FROM notifications
			WHERE user_id = $1
			  AND job_id = $2
			  AND status IN ('dispatched', 'sent')
		)
		ON CONFLICT (user_id, job_id) DO UPDATE SET feedback = EXCLUDED.feedback, created_at = NOW()`
	tag, err := tx.Exec(ctx, q, userID, jobID, string(fb))
	if err != nil {
		return fmt.Errorf("upsert feedback: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return port.ErrFeedbackNotAllowed
	}
	if err := r.rebuildTagAffinity(ctx, tx, userID); err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("commit feedback tx: %w", err)
	}
	return nil
}

// StatsRecent возвращает счётчики good/bad для пользователя за последний период within.
func (r *FeedbackRepository) StatsRecent(ctx context.Context, userID int64, within time.Duration) (port.FeedbackStats, error) {
	const q = `
		SELECT
			COUNT(*) FILTER (WHERE feedback = 'good') AS good_count,
			COUNT(*) FILTER (WHERE feedback = 'bad')  AS bad_count
		FROM user_feedback
		WHERE user_id = $1
		  AND created_at >= NOW() - $2::interval`
	row := r.pool.QueryRow(ctx, q, userID, fmt.Sprintf("%f seconds", within.Seconds()))
	var stats port.FeedbackStats
	if err := row.Scan(&stats.GoodCount, &stats.BadCount); err != nil {
		return port.FeedbackStats{}, fmt.Errorf("stats recent: %w", err)
	}
	return stats, nil
}

// GlobalStatsRecent возвращает счётчики good/bad по всем пользователям за последний период within.
func (r *FeedbackRepository) GlobalStatsRecent(ctx context.Context, within time.Duration) (port.FeedbackStats, error) {
	const q = `
		SELECT
			COUNT(*) FILTER (WHERE feedback = 'good') AS good_count,
			COUNT(*) FILTER (WHERE feedback = 'bad')  AS bad_count
		FROM user_feedback
		WHERE created_at >= NOW() - $1::interval`
	row := r.pool.QueryRow(ctx, q, fmt.Sprintf("%f seconds", within.Seconds()))
	var stats port.FeedbackStats
	if err := row.Scan(&stats.GoodCount, &stats.BadCount); err != nil {
		return port.FeedbackStats{}, fmt.Errorf("global stats recent: %w", err)
	}
	return stats, nil
}

func (r *FeedbackRepository) rebuildTagAffinity(ctx context.Context, tx pgxTx, userID int64) error {
	const upsertAffinitySQL = `
		WITH aggregated AS (
			SELECT
				uf.user_id,
				lower(trim(tag.value)) AS tag,
				COUNT(*) FILTER (WHERE uf.feedback = 'good')::int AS good_count,
				COUNT(*) FILTER (WHERE uf.feedback = 'bad')::int  AS bad_count
			FROM user_feedback uf
			JOIN job_embeddings je ON je.job_id = uf.job_id
			CROSS JOIN LATERAL jsonb_array_elements_text(
				COALESCE(je.ai_metadata->'classification'->'technologies', '[]'::jsonb)
			) AS tag(value)
			WHERE uf.user_id = $1
			  AND trim(tag.value) <> ''
			GROUP BY uf.user_id, lower(trim(tag.value))
		)
		INSERT INTO user_tag_affinity (user_id, tag, good_count, bad_count, weight, updated_at)
		SELECT
			user_id,
			tag,
			good_count,
			bad_count,
			CASE
				WHEN good_count + bad_count = 0 THEN 0
				ELSE GREATEST(
					-1,
					LEAST(
						1,
						(good_count::float - bad_count::float) / NULLIF((good_count + bad_count)::float, 0)
					)
				)
			END AS weight,
			NOW()
		FROM aggregated
		ON CONFLICT (user_id, tag) DO UPDATE SET
			good_count = EXCLUDED.good_count,
			bad_count = EXCLUDED.bad_count,
			weight = EXCLUDED.weight,
			updated_at = NOW()
	`
	if _, err := tx.Exec(ctx, upsertAffinitySQL, userID); err != nil {
		return fmt.Errorf("rebuild tag affinity upsert: %w", err)
	}

	const cleanupSQL = `
		DELETE FROM user_tag_affinity uta
		WHERE uta.user_id = $1
		  AND NOT EXISTS (
			  SELECT 1
			  FROM user_feedback uf
			  JOIN job_embeddings je ON je.job_id = uf.job_id
			  CROSS JOIN LATERAL jsonb_array_elements_text(
				  COALESCE(je.ai_metadata->'classification'->'technologies', '[]'::jsonb)
			  ) AS tag(value)
			  WHERE uf.user_id = uta.user_id
			    AND lower(trim(tag.value)) = uta.tag
		  )
	`
	if _, err := tx.Exec(ctx, cleanupSQL, userID); err != nil {
		return fmt.Errorf("rebuild tag affinity cleanup: %w", err)
	}
	return nil
}

type pgxTx interface {
	Exec(ctx context.Context, sql string, arguments ...any) (pgconn.CommandTag, error)
}

var _ port.FeedbackRepository = (*FeedbackRepository)(nil)
