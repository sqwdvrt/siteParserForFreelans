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

// Record вставляет запись в notifications. inserted=true при успешной вставке, false при дубликате.
func (r *NotificationRepository) Record(ctx context.Context, userID, jobID int64, matchScore float64) (bool, error) {
	var id int64
	err := r.pool.QueryRow(ctx, `
		INSERT INTO notifications (user_id, job_id, match_score)
		VALUES ($1, $2, $3)
		ON CONFLICT (user_id, job_id) DO NOTHING
		RETURNING id
	`, userID, jobID, matchScore).Scan(&id)
	if err != nil {
		if err == pgx.ErrNoRows {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// Delete удаляет запись из notifications (rollback при неуспешной отправке).
func (r *NotificationRepository) Delete(ctx context.Context, userID, jobID int64) error {
	_, err := r.pool.Exec(ctx, `
		DELETE FROM notifications
		WHERE user_id = $1 AND job_id = $2
	`, userID, jobID)
	return err
}

// SentRecently возвращает true, если пользователю отправляли уведомление в течение within.
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
			WHERE user_id = $1 AND sent_at > NOW() - $2::interval
		)
	`, userID, intervalStr).Scan(&exists)
	return exists, err
}

// CountToday возвращает количество уведомлений пользователю за сегодня (UTC).
func (r *NotificationRepository) CountToday(ctx context.Context, userID int64) (int, error) {
	var n int
	err := r.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM notifications
		WHERE user_id = $1 AND sent_at::date = CURRENT_DATE
	`, userID).Scan(&n)
	return n, err
}
