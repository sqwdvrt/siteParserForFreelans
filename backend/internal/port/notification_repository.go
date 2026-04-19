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

// MissedNotification stores jobs preserved after rate/daily-limit drops.
type MissedNotification struct {
	ID            int64
	UserID        int64
	JobID         int64
	MatchScore    float64
	FinalScore    float64
	RankerVersion string
	ReasonCodes   []string
	WhyItFits     string
	JobStatus     string
	JobCreatedAt  time.Time
}

// NotificationRepository — репозиторий уведомлений (дедупликация, at-most-once delivery, rate limit).
type NotificationRepository interface {
	// EnsurePending вставляет запись со статусом 'pending', если её ещё нет.
	// Если запись уже в terminal delivery state ('dispatched'/'sent') — shouldSend=false.
	// Если запись уже 'pending' — wasInserted=false, shouldSend=true.
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

	// MarkDispatched переводит запись в terminal pre-send state 'dispatched',
	// чтобы suppress retry/dedup before external Telegram side effect.
	MarkDispatched(ctx context.Context, userID, jobID int64) error

	// MarkSent переводит запись из 'dispatched' в 'sent' и фиксирует время успешного ответа Telegram API.
	MarkSent(ctx context.Context, userID, jobID int64) error

	// MarkFailed переводит запись в статус 'failed' после перманентной ошибки доставки.
	MarkFailed(ctx context.Context, userID, jobID int64) error

	// Delete удаляет pending-запись (используется при отмене по rate limit / daily limit).
	Delete(ctx context.Context, userID, jobID int64) error

	// SentRecently возвращает true, если пользователю уже финализировали доставку
	// (status='dispatched'/'sent') в течение within.
	SentRecently(ctx context.Context, userID int64, within time.Duration) (bool, error)

	// CountToday возвращает количество финализированных для доставки уведомлений
	// (status='dispatched'/'sent') за текущие сутки (UTC).
	CountToday(ctx context.Context, userID int64) (int, error)

	// GetPendingForUser возвращает все pending-записи пользователя для дайджеста.
	GetPendingForUser(ctx context.Context, userID int64) ([]PendingNotification, error)

	// ClaimPendingDigestNotifications атомарно резервирует до limit pending-записей
	// пользователя для отправки дайджеста, переводя их во внутреннее состояние "sending".
	ClaimPendingDigestNotifications(ctx context.Context, userID int64, limit int) ([]PendingNotification, error)

	// ReleasePendingDigestNotifications возвращает ранее зарезервированные digest-записи
	// обратно в pending после неуспешной отправки batch.
	ReleasePendingDigestNotifications(ctx context.Context, userID int64, jobIDs []int64) error

	// ReclaimStaleDigestClaims возвращает зависшие digest-claims из sending обратно в pending.
	ReclaimStaleDigestClaims(ctx context.Context, olderThan time.Duration) (int64, error)

	// CancelPendingByJobIDs удаляет все pending-уведомления для указанных job_id.
	// Вызывается при экспирации jobs, чтобы не отправлять уведомления о закрытых вакансиях.
	CancelPendingByJobIDs(ctx context.Context, jobIDs []int64) (int64, error)

	// MarkMissed переводит запись в backlog-статус 'missed' после rate/daily-limit skip.
	MarkMissed(ctx context.Context, userID, jobID int64) error

	// GetMissedForUser возвращает backlog missed-записей пользователя с данными о job.
	GetMissedForUser(ctx context.Context, userID int64) ([]MissedNotification, error)

	// ConvertMissedToPending переводит указанные missed-записи обратно в pending.
	ConvertMissedToPending(ctx context.Context, notificationIDs []int64) (int64, error)

	// DeleteNotifications удаляет записи notifications по их id.
	DeleteNotifications(ctx context.Context, notificationIDs []int64) (int64, error)

	// GetFreeUsersWithPendingNotifications возвращает IDs не-pro пользователей,
	// у которых есть pending-уведомления и которые не на паузе.
	GetFreeUsersWithPendingNotifications(ctx context.Context) ([]int64, error)
}
