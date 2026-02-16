package port

import (
	"context"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

type JobRepository interface {
	Save(ctx context.Context, job *domain.Job) (int64, error)
	GetByID(ctx context.Context, id int64) (*domain.Job, error)
	ExistsByURL(ctx context.Context, url string) (bool, error)
}
