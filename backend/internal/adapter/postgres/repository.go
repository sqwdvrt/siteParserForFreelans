package postgres

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// JobRepository реализует port.JobRepository для PostgreSQL.
type JobRepository struct {
	pool *pgxpool.Pool
}

// NewJobRepository создаёт репозиторий с пулом соединений.
func NewJobRepository(pool *pgxpool.Pool) *JobRepository {
	return &JobRepository{pool: pool}
}

// Save сохраняет job в БД. При дубликате по URL обновляет raw_html и возвращает существующий id.
func (r *JobRepository) Save(ctx context.Context, job *domain.Job) (int64, error) {
	var id int64
	err := r.pool.QueryRow(ctx, `
		INSERT INTO jobs (source, url, external_id, title, description, budget, skills, posted_at, raw_html)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		ON CONFLICT (url) DO UPDATE SET raw_html = EXCLUDED.raw_html
		RETURNING id
	`,
		job.Source,
		job.URL,
		nullIfEmpty(job.ExternalID),
		job.Title,
		nullIfEmpty(job.Description),
		nullIfEmpty(job.Budget),
		job.Skills,
		job.PostedAt,
		job.RawHTML,
	).Scan(&id)
	if err != nil {
		return 0, err
	}
	return id, nil
}

// GetByID возвращает job по id. nil при отсутствии.
func (r *JobRepository) GetByID(ctx context.Context, id int64) (*domain.Job, error) {
	var j domain.Job
	err := r.pool.QueryRow(ctx, `
		SELECT id, source, url, COALESCE(external_id, ''), title, COALESCE(description, ''),
			COALESCE(budget, ''), skills, posted_at, raw_html, created_at
		FROM jobs WHERE id = $1
	`, id).Scan(&j.ID, &j.Source, &j.URL, &j.ExternalID, &j.Title, &j.Description,
		&j.Budget, &j.Skills, &j.PostedAt, &j.RawHTML, &j.CreatedAt)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &j, nil
}

// ExistsByURL проверяет наличие job по URL.
func (r *JobRepository) ExistsByURL(ctx context.Context, url string) (bool, error) {
	var exists bool
	err := r.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM jobs WHERE url = $1)`, url).Scan(&exists)
	return exists, err
}

func nullIfEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
