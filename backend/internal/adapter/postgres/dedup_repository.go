package postgres

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// JobDedupRepository handles cross-platform job deduplication queries.
type JobDedupRepository struct {
	pool *pgxpool.Pool
}

// NewJobDedupRepository creates a new dedup repository.
func NewJobDedupRepository(pool *pgxpool.Pool) *JobDedupRepository {
	return &JobDedupRepository{pool: pool}
}

// FindSimilarJob checks for a cross-platform duplicate using the find_similar_job SQL function.
// Returns (canonicalJobID, similarity) or (0, 0) if no duplicate found.
func (r *JobDedupRepository) FindSimilarJob(ctx context.Context, title, description, budget, excludeSource string) (int64, float64, error) {
	var jobID int64
	var similarity float64
	err := r.pool.QueryRow(ctx,
		`SELECT job_id, similarity_score FROM find_similar_job($1, $2, $3, $4)`,
		title, description, budget, excludeSource,
	).Scan(&jobID, &similarity)
	if err != nil {
		// No rows = no duplicate found, which is OK
		return 0, 0, nil
	}
	if jobID == 0 {
		return 0, 0, nil
	}
	return jobID, similarity, nil
}

// MergeDuplicateSource updates the duplicate tracking when a job appears on multiple platforms.
func (r *JobDedupRepository) MergeDuplicateSource(ctx context.Context, canonicalID, duplicateID int64, similarity float64) error {
	_, err := r.pool.Exec(ctx,
		`INSERT INTO job_duplicates (canonical_id, duplicate_id, similarity)
		 VALUES ($1, $2, $3)
		 ON CONFLICT (canonical_id, duplicate_id) DO NOTHING`,
		canonicalID, duplicateID, similarity,
	)
	return err
}

// GetDuplicateSources returns all sources where a job was found as duplicates.
func (r *JobDedupRepository) GetDuplicateSources(ctx context.Context, canonicalID int64) ([]int64, error) {
	rows, err := r.pool.Query(ctx,
		`SELECT duplicate_id FROM job_duplicates WHERE canonical_id = $1`,
		canonicalID,
	)
	if err != nil {
		return nil, fmt.Errorf("query duplicate sources: %w", err)
	}
	defer rows.Close()

	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("scan duplicate source: %w", err)
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}
