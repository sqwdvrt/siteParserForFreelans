package port

import (
	"context"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// FeedbackStats агрегирует обратную связь пользователя за период.
type FeedbackStats struct {
	GoodCount int
	BadCount  int
}

// FeedbackRepository хранит и читает обратную связь пользователей на уведомления.
type FeedbackRepository interface {
	// Upsert записывает или обновляет feedback для пары (user_id, job_id).
	// Повторный вызов для той же пары перезаписывает предыдущую оценку.
	Upsert(ctx context.Context, userID, jobID int64, fb domain.FeedbackType) error

	// StatsRecent возвращает агрегированные счётчики good/bad для пользователя
	// за последний период within.
	StatsRecent(ctx context.Context, userID int64, within time.Duration) (FeedbackStats, error)

	// GlobalStatsRecent возвращает агрегированные счётчики good/bad по всем
	// пользователям за последний период within.
	GlobalStatsRecent(ctx context.Context, within time.Duration) (FeedbackStats, error)
}
