package usecase

import (
	"context"
	"log/slog"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// ExpireJobs помечает неактивные проекты как 'expired' и отменяет pending-уведомления.
type ExpireJobs struct {
	repo          port.JobRepository
	notifRepo     port.NotificationRepository
	olderThanDays int
}

// NewExpireJobs создаёт use case. olderThanDays — порог в днях без last_seen_at.
// Значение <= 0 заменяется безопасным дефолтом (14 дней).
func NewExpireJobs(repo port.JobRepository, notifRepo port.NotificationRepository, olderThanDays int) *ExpireJobs {
	if olderThanDays <= 0 {
		olderThanDays = 14
	}
	return &ExpireJobs{repo: repo, notifRepo: notifRepo, olderThanDays: olderThanDays}
}

// Execute запускает экспирацию и каскадную отмену pending-уведомлений.
func (e *ExpireJobs) Execute(ctx context.Context) (int64, error) {
	expiredIDs, err := e.repo.ExpireStaleJobs(ctx, e.olderThanDays)
	if err != nil {
		return 0, err
	}
	n := int64(len(expiredIDs))
	if n > 0 {
		slog.Info("expire_jobs: marked stale jobs as expired", "count", n, "older_than_days", e.olderThanDays)
		if cancelled, err := e.notifRepo.CancelPendingByJobIDs(ctx, expiredIDs); err != nil {
			slog.Warn("expire_jobs: cancel pending notifications failed", "err", err)
		} else if cancelled > 0 {
			slog.Info("expire_jobs: cancelled pending notifications", "count", cancelled)
		}
	}
	return n, nil
}
