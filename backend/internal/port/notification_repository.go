package port

import (
	"context"
	"time"
)

// PendingNotification — pending-запись для дайджеста pro-пользователя.
type PendingNotification struct {
	JobID      int64
	MatchScore float64
	WhyItFits  string
}

// NotificationRepository — репозиторий уведомлений (дедупликация, retry, rate limit).
type NotificationRepository interface {
	// EnsurePending вставляет запись со статусом 'pending', если её ещё нет.
	// Если запись уже 'sent' — shouldSend=false (уже доставлено, пропустить).
	// Если запись уже 'pending' — wasInserted=false, shouldSend=true (retry, rate limit не применяется).
	// Если записи не было — wasInserted=true, shouldSend=true (новое уведомление).
	EnsurePending(
		ctx context.Context,
		userID, jobID int64,
		matchScore float64,
		finalScore float64,
		rankerVersion string,
		reasonCodes []string,
		whyItFits string,
	) (wasInserted bool, shouldSend bool, err error)

	// MarkSent переводит запись в статус 'sent' и фиксирует время доставки.
	// Вызывается только после подтверждённой доставки в Telegram.
	MarkSent(ctx context.Context, userID, jobID int64) error

	// Delete удаляет pending-запись (используется при отмене по rate limit / daily limit).
	Delete(ctx context.Context, userID, jobID int64) error

	// SentRecently возвращает true, если пользователю успешно (status='sent') отправляли
	// уведомление в течение within.
	SentRecently(ctx context.Context, userID int64, within time.Duration) (bool, error)

	// CountToday возвращает количество успешно (status='sent') отправленных уведомлений
	// пользователю за текущие сутки (UTC).
	CountToday(ctx context.Context, userID int64) (int, error)

	// GetPendingForUser возвращает все pending-записи пользователя для дайджеста.
	GetPendingForUser(ctx context.Context, userID int64) ([]PendingNotification, error)

	// CancelPendingByJobIDs удаляет все pending-уведомления для указанных job_id.
	// Вызывается при экспирации jobs, чтобы не отправлять уведомления о закрытых вакансиях.
	CancelPendingByJobIDs(ctx context.Context, jobIDs []int64) (int64, error)
}
