package postgres

import (
	"context"
	"fmt"
	"time"

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
	const q = `
		INSERT INTO user_feedback (user_id, job_id, feedback)
		VALUES ($1, $2, $3)
		ON CONFLICT (user_id, job_id) DO UPDATE SET feedback = EXCLUDED.feedback, created_at = NOW()`
	if _, err := r.pool.Exec(ctx, q, userID, jobID, string(fb)); err != nil {
		return fmt.Errorf("upsert feedback: %w", err)
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

var _ port.FeedbackRepository = (*FeedbackRepository)(nil)
