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
	// GetUnembeddedIDs returns IDs of jobs that have no entry in job_embeddings.
	// Used by the crawler to recover jobs that were saved but never enqueued.
	GetUnembeddedIDs(ctx context.Context, limit int) ([]int64, error)
}
