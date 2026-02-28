package postgres

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
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
func (r *NotificationRepository) EnsurePending(ctx context.Context, userID, jobID int64, matchScore float64) (bool, bool, error) {
	var id int64
	err := r.pool.QueryRow(ctx, `
		INSERT INTO notifications (user_id, job_id, match_score, status)
		VALUES ($1, $2, $3, 'pending')
		ON CONFLICT (user_id, job_id) DO NOTHING
		RETURNING id
	`, userID, jobID, matchScore).Scan(&id)
	if err == nil {
		// Успешно вставлено новое pending-уведомление.
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
