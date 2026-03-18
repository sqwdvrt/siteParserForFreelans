package usecase

import (
	"context"
	"log/slog"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// ExpireJobs помечает неактивные проекты как 'expired'.
type ExpireJobs struct {
	repo          port.JobRepository
	olderThanDays int
}

// NewExpireJobs создаёт use case. olderThanDays — порог в днях без last_seen_at.
// Значение <= 0 заменяется безопасным дефолтом (14 дней) во избежание массовой
// экспирации всех активных jobs при некорректном конфиге.
func NewExpireJobs(repo port.JobRepository, olderThanDays int) *ExpireJobs {
	if olderThanDays <= 0 {
		olderThanDays = 14
	}
	return &ExpireJobs{repo: repo, olderThanDays: olderThanDays}
}

// Execute запускает экспирацию. Возвращает количество обновлённых jobs.
func (e *ExpireJobs) Execute(ctx context.Context) (int64, error) {
	n, err := e.repo.ExpireStaleJobs(ctx, e.olderThanDays)
	if err != nil {
		return 0, err
	}
	if n > 0 {
		slog.Info("expire_jobs: marked stale jobs as expired", "count", n, "older_than_days", e.olderThanDays)
	}
	return n, nil
}
