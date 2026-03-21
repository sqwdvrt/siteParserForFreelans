package postgres

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// DispatchRepository coordinates transactional staging of queue-bound work in Postgres.
type DispatchRepository struct {
	pool *pgxpool.Pool
}

const defaultDispatchLease = 10 * time.Minute

func NewDispatchRepository(pool *pgxpool.Pool) *DispatchRepository {
	return &DispatchRepository{pool: pool}
}

func (r *DispatchRepository) UpdateProfileScopedAndStage(
	ctx context.Context,
	userID int64,
	profileText string,
	trace port.QueueDispatchTrace,
) error {
	trace = normalizeDispatchTrace(trace)
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, setUserScopeSQL, fmt.Sprintf("%d", userID)); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `
		UPDATE users SET profile_text = $1, updated_at = NOW() WHERE id = $2
	`, profileText, userID); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO pending_user_embeds (user_id, trace_id, traceparent, created_at, queued_at)
		VALUES ($1, NULLIF($2, ''), NULLIF($3, ''), NOW(), NULL)
		ON CONFLICT (user_id) DO UPDATE SET
			trace_id = COALESCE(NULLIF(EXCLUDED.trace_id, ''), pending_user_embeds.trace_id),
			traceparent = COALESCE(NULLIF(EXCLUDED.traceparent, ''), pending_user_embeds.traceparent),
			created_at = NOW(),
			queued_at = NULL
	`, userID, trace.TraceID, trace.Traceparent); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (r *DispatchRepository) SaveAndStageJobForEmbedding(
	ctx context.Context,
	job *domain.Job,
	trace port.QueueDispatchTrace,
) (int64, bool, error) {
	trace = normalizeDispatchTrace(trace)
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return 0, false, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var id int64
	err = tx.QueryRow(ctx, `
		INSERT INTO jobs (source, url, external_id, title, description, budget, skills, posted_at, raw_html)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		ON CONFLICT (url) DO NOTHING
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
	switch err {
	case nil:
		if stageErr := r.stageJobForEmbeddingTx(ctx, tx, id, trace); stageErr != nil {
			return 0, false, stageErr
		}
		if commitErr := tx.Commit(ctx); commitErr != nil {
			return 0, false, commitErr
		}
		return id, true, nil
	case pgx.ErrNoRows:
		if queryErr := tx.QueryRow(ctx, `
			UPDATE jobs
			SET source = $2,
			    external_id = $3,
			    title = $4,
			    description = $5,
			    budget = $6,
			    skills = $7,
			    posted_at = $8,
			    raw_html = $9,
			    status = 'active',
			    last_seen_at = NOW()
			WHERE url = $1
			RETURNING id
		`,
			job.URL,
			job.Source,
			nullIfEmpty(job.ExternalID),
			job.Title,
			nullIfEmpty(job.Description),
			nullIfEmpty(job.Budget),
			job.Skills,
			job.PostedAt,
			job.RawHTML,
		).Scan(&id); queryErr != nil {
			return 0, false, queryErr
		}
		if stageErr := r.stageJobForEmbeddingTx(ctx, tx, id, trace); stageErr != nil {
			return 0, false, stageErr
		}
		if commitErr := tx.Commit(ctx); commitErr != nil {
			return 0, false, commitErr
		}
		return id, false, nil
	default:
		return 0, false, err
	}
}

func (r *DispatchRepository) StageJobForEmbedding(
	ctx context.Context,
	jobID int64,
	trace port.QueueDispatchTrace,
) error {
	trace = normalizeDispatchTrace(trace)
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if err := r.stageJobForEmbeddingTx(ctx, tx, jobID, trace); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (r *DispatchRepository) stageJobForEmbeddingTx(
	ctx context.Context,
	tx pgx.Tx,
	jobID int64,
	trace port.QueueDispatchTrace,
) error {
	if _, err := tx.Exec(ctx, `
		INSERT INTO pending_job_embeds (job_id, trace_id, traceparent, created_at, queued_at)
		VALUES ($1, NULLIF($2, ''), NULLIF($3, ''), NOW(), NULL)
		ON CONFLICT (job_id) DO UPDATE SET
			trace_id = COALESCE(NULLIF(EXCLUDED.trace_id, ''), pending_job_embeds.trace_id),
			traceparent = COALESCE(NULLIF(EXCLUDED.traceparent, ''), pending_job_embeds.traceparent),
			created_at = NOW(),
			queued_at = NULL
	`, jobID, trace.TraceID, trace.Traceparent); err != nil {
		return err
	}
	return nil
}

func (r *DispatchRepository) ClaimPendingUserEmbeds(
	ctx context.Context,
	limit int,
	lease time.Duration,
) ([]port.PendingUserEmbed, error) {
	if limit <= 0 {
		return []port.PendingUserEmbed{}, nil
	}
	rows, err := r.pool.Query(ctx, `
		WITH claimable AS (
			SELECT user_id
			FROM pending_user_embeds
			WHERE queued_at IS NULL OR queued_at < NOW() - make_interval(secs => $2::int)
			ORDER BY created_at
			LIMIT $1
			FOR UPDATE SKIP LOCKED
		)
		UPDATE pending_user_embeds p
		SET queued_at = NOW()
		FROM claimable c
		WHERE p.user_id = c.user_id
		RETURNING p.user_id, COALESCE(p.trace_id, ''), COALESCE(p.traceparent, '')
	`, limit, leaseSeconds(lease))
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []port.PendingUserEmbed
	for rows.Next() {
		var item port.PendingUserEmbed
		if err := rows.Scan(&item.UserID, &item.Trace.TraceID, &item.Trace.Traceparent); err != nil {
			return nil, err
		}
		item.Trace = normalizeDispatchTrace(item.Trace)
		result = append(result, item)
	}
	return result, rows.Err()
}

func (r *DispatchRepository) DeletePendingUserEmbeds(ctx context.Context, userIDs []int64) error {
	if len(userIDs) == 0 {
		return nil
	}
	_, err := r.pool.Exec(ctx, `DELETE FROM pending_user_embeds WHERE user_id = ANY($1)`, userIDs)
	return err
}

func (r *DispatchRepository) ReleasePendingUserEmbeds(ctx context.Context, userIDs []int64) error {
	if len(userIDs) == 0 {
		return nil
	}
	_, err := r.pool.Exec(ctx, `UPDATE pending_user_embeds SET queued_at = NULL WHERE user_id = ANY($1)`, userIDs)
	return err
}

func (r *DispatchRepository) ClaimPendingJobEmbeds(
	ctx context.Context,
	limit int,
	lease time.Duration,
) ([]port.PendingJobEmbed, error) {
	if limit <= 0 {
		return []port.PendingJobEmbed{}, nil
	}
	rows, err := r.pool.Query(ctx, `
		WITH claimable AS (
			SELECT job_id
			FROM pending_job_embeds
			WHERE queued_at IS NULL OR queued_at < NOW() - make_interval(secs => $2::int)
			ORDER BY created_at
			LIMIT $1
			FOR UPDATE SKIP LOCKED
		)
		UPDATE pending_job_embeds p
		SET queued_at = NOW()
		FROM claimable c
		WHERE p.job_id = c.job_id
		RETURNING p.job_id, COALESCE(p.trace_id, ''), COALESCE(p.traceparent, '')
	`, limit, leaseSeconds(lease))
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []port.PendingJobEmbed
	for rows.Next() {
		var item port.PendingJobEmbed
		if err := rows.Scan(&item.JobID, &item.Trace.TraceID, &item.Trace.Traceparent); err != nil {
			return nil, err
		}
		item.Trace = normalizeDispatchTrace(item.Trace)
		result = append(result, item)
	}
	return result, rows.Err()
}

func (r *DispatchRepository) DeletePendingJobEmbeds(ctx context.Context, jobIDs []int64) error {
	if len(jobIDs) == 0 {
		return nil
	}
	_, err := r.pool.Exec(ctx, `DELETE FROM pending_job_embeds WHERE job_id = ANY($1)`, jobIDs)
	return err
}

func (r *DispatchRepository) ReleasePendingJobEmbeds(ctx context.Context, jobIDs []int64) error {
	if len(jobIDs) == 0 {
		return nil
	}
	_, err := r.pool.Exec(ctx, `UPDATE pending_job_embeds SET queued_at = NULL WHERE job_id = ANY($1)`, jobIDs)
	return err
}

func leaseSeconds(lease time.Duration) int {
	secs := int(lease.Seconds())
	if secs <= 0 {
		return int(defaultDispatchLease / time.Second)
	}
	return secs
}

func normalizeDispatchTrace(trace port.QueueDispatchTrace) port.QueueDispatchTrace {
	return port.QueueDispatchTrace{
		TraceID:     normalizeTraceField(trace.TraceID, 128),
		Traceparent: normalizeTraceField(trace.Traceparent, 512),
	}
}

func normalizeTraceField(raw string, limit int) string {
	value := strings.TrimSpace(raw)
	if limit > 0 && len(value) > limit {
		value = value[:limit]
	}
	return value
}
