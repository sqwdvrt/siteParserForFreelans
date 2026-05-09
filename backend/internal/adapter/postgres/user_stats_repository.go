package postgres

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const defaultUserStatsWindow = 7 * 24 * time.Hour

// UserStatsRepository implements port.UserStatsRepository for PostgreSQL.
type UserStatsRepository struct {
	pool *pgxpool.Pool
}

// NewUserStatsRepository creates a repository for user-facing /stats numbers.
func NewUserStatsRepository(pool *pgxpool.Pool) *UserStatsRepository {
	return &UserStatsRepository{pool: pool}
}

// GetUserStats aggregates explainable per-user stats for the requested time window.
func (r *UserStatsRepository) GetUserStats(
	ctx context.Context,
	userID int64,
	window time.Duration,
) (*port.UserStats, error) {
	if window <= 0 {
		window = defaultUserStatsWindow
	}
	since := time.Now().Add(-window)
	stats := &port.UserStats{
		PeriodDays: int(window.Hours() / 24),
	}

	if err := r.pool.QueryRow(ctx, `
		WITH found_jobs AS (
			SELECT DISTINCT job_id
			FROM pending_ac_jobs
			WHERE user_id = $1
			  AND created_at >= $2
			UNION
			SELECT DISTINCT job_id
			FROM user_job_filter_events
			WHERE user_id = $1
			  AND reason = 'budget'
			  AND created_at >= $2
		)
		SELECT COUNT(*)
		FROM found_jobs
	`, userID, since).Scan(&stats.ProjectsFound); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `
		SELECT COUNT(DISTINCT job_id)
		FROM notifications
		WHERE user_id = $1
		  AND status IN ('dispatched', 'sent')
		  AND sent_at IS NOT NULL
		  AND sent_at >= $2
	`, userID, since).Scan(&stats.ProjectsShown); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `
		SELECT COUNT(DISTINCT job_id)
		FROM user_job_filter_events
		WHERE user_id = $1
		  AND reason = 'budget'
		  AND created_at >= $2
	`, userID, since).Scan(&stats.ProjectsFilteredByBudget); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `
		SELECT COUNT(DISTINCT job_id)
		FROM notifications
		WHERE user_id = $1
		  AND status = 'missed'
		  AND created_at >= $2
	`, userID, since).Scan(&stats.ProjectsHiddenByCap); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM user_preferences
			WHERE user_id = $1
			  AND (min_budget IS NOT NULL OR max_budget IS NOT NULL)
		)
	`, userID).Scan(&stats.BudgetFilterActive); err != nil {
		return nil, err
	}

	filteredOther := stats.ProjectsFound - stats.ProjectsShown - stats.ProjectsFilteredByBudget
	if filteredOther > 0 {
		stats.ProjectsFilteredOther = filteredOther
	}

	return stats, nil
}
