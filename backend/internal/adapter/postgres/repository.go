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

// GetByIDs возвращает map[id]*Job для всех найденных id. Один SQL-запрос.
func (r *JobRepository) GetByIDs(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
	if len(ids) == 0 {
		return map[int64]*domain.Job{}, nil
	}
	rows, err := r.pool.Query(ctx, `
		SELECT id, source, url, COALESCE(external_id, ''), title, COALESCE(description, ''),
			COALESCE(budget, ''), skills, posted_at, raw_html, created_at
		FROM jobs WHERE id = ANY($1)
	`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make(map[int64]*domain.Job, len(ids))
	for rows.Next() {
		var j domain.Job
		if err := rows.Scan(&j.ID, &j.Source, &j.URL, &j.ExternalID, &j.Title, &j.Description,
			&j.Budget, &j.Skills, &j.PostedAt, &j.RawHTML, &j.CreatedAt); err != nil {
			return nil, err
		}
		result[j.ID] = &j
	}
	return result, rows.Err()
}

// ExistsByURL проверяет наличие job по URL.
func (r *JobRepository) ExistsByURL(ctx context.Context, url string) (bool, error) {
	var exists bool
	err := r.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM jobs WHERE url = $1)`, url).Scan(&exists)
	return exists, err
}

// GetUnembeddedIDs returns IDs of jobs without a job_embeddings entry (orphaned jobs).
func (r *JobRepository) GetUnembeddedIDs(ctx context.Context, limit int) ([]int64, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := r.pool.Query(ctx, `
		SELECT j.id FROM jobs j
		LEFT JOIN job_embeddings je ON je.job_id = j.id
		WHERE je.job_id IS NULL
		ORDER BY j.created_at DESC
		LIMIT $1
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

func nullIfEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
