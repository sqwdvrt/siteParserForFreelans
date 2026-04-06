package port

import (
	"context"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

type JobRepository interface {
	Save(ctx context.Context, job *domain.Job) (int64, error)
	GetByID(ctx context.Context, id int64) (*domain.Job, error)
	GetByIDs(ctx context.Context, ids []int64) (map[int64]*domain.Job, error)
	ExistsByURL(ctx context.Context, url string) (bool, error)
	// TouchSeenAt обновляет last_seen_at = NOW() для job по URL.
	// Вызывается краулером при каждом обходе, даже если job уже существует.
	TouchSeenAt(ctx context.Context, url string) error
	// TouchSeenAtByID обновляет last_seen_at = NOW() для job по ID.
	// Используется при кросс-платформенной дедупликации.
	TouchSeenAtByID(ctx context.Context, id int64) error
	// ExpireStaleJobs помечает активные jobs как 'expired' если last_seen_at
	// старше olderThanDays дней. Возвращает ID обновлённых jobs.
	ExpireStaleJobs(ctx context.Context, olderThanDays int) ([]int64, error)
	// GetUnembeddedIDs returns IDs of jobs that have no entry in job_embeddings.
	// Used by the crawler to recover jobs that were saved but never enqueued.
	GetUnembeddedIDs(ctx context.Context, limit int) ([]int64, error)
	// ExpireByURL мгновенно помечает job как 'expired' по URL.
	// Вызывается при получении 404/410 чтобы не ждать TTL.
	// Реализация должна атомарно зафиксировать и экспирацию job, и очистку pending-уведомлений.
	// Возвращает IDs затронутых jobs.
	ExpireByURL(ctx context.Context, url string) ([]int64, error)
}
