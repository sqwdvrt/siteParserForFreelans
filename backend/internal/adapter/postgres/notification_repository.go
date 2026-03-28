package postgres

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// NotificationRepository реализует port.NotificationRepository.
type NotificationRepository struct {
	pool *pgxpool.Pool
}

// NewNotificationRepository создаёт репозиторий уведомлений.
func NewNotificationRepository(pool *pgxpool.Pool) *NotificationRepository {
	return &NotificationRepository{pool: pool}
}

// EnsurePending вставляет запись со статусом 'pending', если её нет.
// Возвращает (wasInserted, shouldSend, err).
func (r *NotificationRepository) EnsurePending(
	ctx context.Context,
	userID, jobID int64,
	matchScore float64,
	finalScore float64,
	rankerVersion string,
	reasonCodes []string,
	whyItFits string,
) (bool, bool, error) {
	var id int64
	err := r.pool.QueryRow(ctx, `
		INSERT INTO notifications (
			user_id,
			job_id,
			match_score,
			final_score,
			ranker_version,
			reason_codes,
			why_it_fits,
			sent_at,
			status
		)
		VALUES ($1, $2, $3, $4, NULLIF($5, ''), $6, NULLIF($7, ''), NULL, 'pending')
		ON CONFLICT (user_id, job_id) DO NOTHING
		RETURNING id
	`, userID, jobID, matchScore, finalScore, rankerVersion, reasonCodes, whyItFits).Scan(&id)
	if err == nil {
		return true, true, nil
	}
	if err != pgx.ErrNoRows {
		return false, false, err
	}
	// Конфликт — запись уже существует. Смотрим статус.
	var status string
	err = r.pool.QueryRow(ctx, `
		SELECT status FROM notifications WHERE user_id = $1 AND job_id = $2
	`, userID, jobID).Scan(&status)
	if err != nil {
		return false, false, err
	}
	if status == "pending" {
		if _, err := r.pool.Exec(ctx, `
			UPDATE notifications
			SET
				match_score = GREATEST(COALESCE(match_score, 0), $3),
				final_score = GREATEST(COALESCE(final_score, 0), $4),
				ranker_version = COALESCE(NULLIF($5, ''), ranker_version),
				reason_codes = CASE
					WHEN array_length($6::text[], 1) IS NULL THEN reason_codes
					ELSE $6
				END,
				why_it_fits = COALESCE(NULLIF($7, ''), why_it_fits)
			WHERE user_id = $1 AND job_id = $2 AND status = 'pending'
		`, userID, jobID, matchScore, finalScore, rankerVersion, reasonCodes, whyItFits); err != nil {
			return false, false, err
		}
	}
	if status == "sending" || status == "dispatched" {
		return false, false, nil
	}
	if status == "failed" {
		if _, err := r.pool.Exec(ctx, `
			UPDATE notifications
			SET
				status = 'pending',
				claimed_at = NULL,
				sent_at = NULL,
				match_score = $3,
				final_score = $4,
				ranker_version = NULLIF($5, ''),
				reason_codes = $6,
				why_it_fits = NULLIF($7, '')
			WHERE user_id = $1 AND job_id = $2 AND status = 'failed'
		`, userID, jobID, matchScore, finalScore, rankerVersion, reasonCodes, whyItFits); err != nil {
			return false, false, err
		}
		return true, true, nil
	}
	// pending → нужно повторить отправку; sent → уже доставлено.
	return false, status == "pending", nil
}

// MarkDispatched transitions a notification into terminal pre-send state.
func (r *NotificationRepository) MarkDispatched(ctx context.Context, userID, jobID int64) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE notifications
		SET status = 'dispatched',
		    claimed_at = NULL,
		    sent_at = COALESCE(sent_at, NOW())
		WHERE user_id = $1 AND job_id = $2
		  AND status IN ('pending', 'sending')
	`, userID, jobID)
	return err
}

// MarkSent переводит запись в статус 'sent' и обновляет sent_at до момента успешного ответа Telegram API.
func (r *NotificationRepository) MarkSent(ctx context.Context, userID, jobID int64) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE notifications SET status = 'sent', claimed_at = NULL, sent_at = COALESCE(sent_at, NOW())
		WHERE user_id = $1 AND job_id = $2 AND status IN ('pending', 'sending', 'dispatched', 'sent')
	`, userID, jobID)
	return err
}

// MarkFailed переводит запись в статус 'failed' и сбрасывает sent_at.
func (r *NotificationRepository) MarkFailed(ctx context.Context, userID, jobID int64) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE notifications SET status = 'failed', claimed_at = NULL, sent_at = NULL
		WHERE user_id = $1 AND job_id = $2
	`, userID, jobID)
	return err
}

// Delete удаляет запись из notifications (отмена по rate limit / daily limit).
func (r *NotificationRepository) Delete(ctx context.Context, userID, jobID int64) error {
	_, err := r.pool.Exec(ctx, `
		DELETE FROM notifications
		WHERE user_id = $1 AND job_id = $2
	`, userID, jobID)
	return err
}

// SentRecently returns true when delivery has already been finalized for the user.
func (r *NotificationRepository) SentRecently(ctx context.Context, userID int64, within time.Duration) (bool, error) {
	secs := int(within.Seconds())
	if secs <= 0 {
		return false, nil
	}
	intervalStr := fmt.Sprintf("%d seconds", secs)
	var exists bool
	err := r.pool.QueryRow(ctx, `
		SELECT EXISTS(
			SELECT 1 FROM notifications
			WHERE user_id = $1 AND status IN ('dispatched', 'sent') AND sent_at > NOW() - $2::interval
		)
	`, userID, intervalStr).Scan(&exists)
	return exists, err
}

// CountToday returns the number of finalized deliveries for the user in current UTC day.
func (r *NotificationRepository) CountToday(ctx context.Context, userID int64) (int, error) {
	var n int
	err := r.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM notifications
		WHERE user_id = $1
		  AND status IN ('dispatched', 'sent')
		  AND sent_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
		  AND sent_at < ((date_trunc('day', now() AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC')
	`, userID).Scan(&n)
	return n, err
}

// CancelPendingByJobIDs удаляет pending-уведомления для экспайренных jobs.
func (r *NotificationRepository) CancelPendingByJobIDs(ctx context.Context, jobIDs []int64) (int64, error) {
	if len(jobIDs) == 0 {
		return 0, nil
	}
	tag, err := r.pool.Exec(ctx,
		`DELETE FROM notifications WHERE job_id = ANY($1) AND status = 'pending'`, jobIDs)
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}

// GetPendingForUser возвращает все pending-записи пользователя для дайджеста (по убыванию final_score).
func (r *NotificationRepository) GetPendingForUser(ctx context.Context, userID int64) ([]port.PendingNotification, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT job_id,
		       COALESCE(final_score, match_score, 0),
		       COALESCE(why_it_fits, '')
		FROM notifications
		WHERE user_id = $1 AND status = 'pending'
		ORDER BY COALESCE(final_score, match_score, 0) DESC
	`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []port.PendingNotification
	for rows.Next() {
		var pn port.PendingNotification
		if err := rows.Scan(&pn.JobID, &pn.MatchScore, &pn.WhyItFits); err != nil {
			return nil, err
		}
		result = append(result, pn)
	}
	return result, rows.Err()
}

// ClaimPendingDigestNotifications atomically reserves pending notifications for digest delivery.
func (r *NotificationRepository) ClaimPendingDigestNotifications(
	ctx context.Context,
	userID int64,
	limit int,
) ([]port.PendingNotification, error) {
	if limit <= 0 {
		return nil, nil
	}
	rows, err := r.pool.Query(ctx, `
		WITH claimed AS (
			SELECT id,
			       job_id,
			       COALESCE(final_score, match_score, 0) AS effective_score,
			       COALESCE(why_it_fits, '') AS why_it_fits
			FROM notifications
			WHERE user_id = $1 AND status = 'pending'
			ORDER BY COALESCE(final_score, match_score, 0) DESC
			LIMIT $2
			FOR UPDATE SKIP LOCKED
		),
		updated AS (
			UPDATE notifications n
			SET status = 'sending',
			    claimed_at = NOW()
			FROM claimed c
			WHERE n.id = c.id
			RETURNING c.job_id, c.effective_score, c.why_it_fits
		)
		SELECT job_id, effective_score, why_it_fits
		FROM updated
		ORDER BY effective_score DESC, job_id ASC
	`, userID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []port.PendingNotification
	for rows.Next() {
		var pn port.PendingNotification
		if err := rows.Scan(&pn.JobID, &pn.MatchScore, &pn.WhyItFits); err != nil {
			return nil, err
		}
		result = append(result, pn)
	}
	return result, rows.Err()
}

// ReleasePendingDigestNotifications returns reserved digest notifications back to pending.
func (r *NotificationRepository) ReleasePendingDigestNotifications(ctx context.Context, userID int64, jobIDs []int64) error {
	if len(jobIDs) == 0 {
		return nil
	}
	_, err := r.pool.Exec(ctx, `
		UPDATE notifications
		SET status = 'pending',
		    claimed_at = NULL
		WHERE user_id = $1
		  AND job_id = ANY($2)
		  AND status = 'sending'
	`, userID, jobIDs)
	return err
}

// ReclaimStaleDigestClaims returns stale digest leases back to pending after olderThan.
func (r *NotificationRepository) ReclaimStaleDigestClaims(ctx context.Context, olderThan time.Duration) (int64, error) {
	secs := int(olderThan.Seconds())
	if secs <= 0 {
		return 0, nil
	}
	intervalStr := fmt.Sprintf("%d seconds", secs)
	tag, err := r.pool.Exec(ctx, `
		UPDATE notifications
		SET status = 'pending',
		    claimed_at = NULL
		WHERE status = 'sending'
		  AND claimed_at IS NOT NULL
		  AND claimed_at < NOW() - $1::interval
	`, intervalStr)
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}
