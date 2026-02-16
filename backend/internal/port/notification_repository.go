package port

import (
	"context"
	"time"
)

// NotificationRepository — репозиторий уведомлений (дедупликация, rate limit).
type NotificationRepository interface {
	// Record записывает уведомление. Возвращает true если вставлено, false при дубликате (user_id, job_id).
	Record(ctx context.Context, userID, jobID int64, matchScore float64) (inserted bool, err error)

	// Delete удаляет запись уведомления (используется для отката при неуспешной отправке).
	Delete(ctx context.Context, userID, jobID int64) error

	// SentRecently возвращает true, если пользователю отправляли уведомление в течение within.
	SentRecently(ctx context.Context, userID int64, within time.Duration) (bool, error)

	// CountToday возвращает количество уведомлений пользователю за сегодня (UTC).
	CountToday(ctx context.Context, userID int64) (int, error)
}
