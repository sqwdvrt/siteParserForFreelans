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
	// pending → нужно повторить отправку; sent → уже доставлено.
	return false, status == "pending", nil
}

// MarkSent переводит запись в статус 'sent' и обновляет sent_at до момента реальной доставки.
func (r *NotificationRepository) MarkSent(ctx context.Context, userID, jobID int64) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE notifications SET status = 'sent', sent_at = NOW()
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

// SentRecently возвращает true, если пользователю успешно отправляли уведомление в течение within.
// Учитываются только записи со статусом 'sent'.
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
			WHERE user_id = $1 AND status = 'sent' AND sent_at > NOW() - $2::interval
		)
	`, userID, intervalStr).Scan(&exists)
	return exists, err
}

// CountToday возвращает количество успешно отправленных уведомлений пользователю за текущие сутки (UTC).
// Учитываются только записи со статусом 'sent'.
func (r *NotificationRepository) CountToday(ctx context.Context, userID int64) (int, error) {
	var n int
	err := r.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM notifications
		WHERE user_id = $1
		  AND status = 'sent'
		  AND sent_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
		  AND sent_at < ((date_trunc('day', now() AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC')
	`, userID).Scan(&n)
	return n, err
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
