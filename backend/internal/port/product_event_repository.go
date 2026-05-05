package port

import "context"
import "time"

type ProductEventType string

const (
	ProductEventUserRegistered         ProductEventType = "user_registered"
	ProductEventProfileUpdated         ProductEventType = "profile_updated"
	ProductEventProfileCompleted       ProductEventType = "profile_completed"
	ProductEventPreferencesUpdated     ProductEventType = "preferences_updated"
	ProductEventNotifyHourUpdated      ProductEventType = "notify_hour_updated"
	ProductEventPauseUpdated           ProductEventType = "pause_updated"
	ProductEventFeedbackSubmitted      ProductEventType = "feedback_submitted"
	ProductEventNotificationSent       ProductEventType = "notification_sent"
	ProductEventProUpgradeViewed       ProductEventType = "pro_upgrade_viewed"
	ProductEventProUpgradeRequested    ProductEventType = "pro_upgrade_requested"
	ProductEventProActivated           ProductEventType = "pro_activated"
	ProductEventProRenewed             ProductEventType = "pro_renewed"
	ProductEventProExpired             ProductEventType = "pro_expired"
	ProductEventProRenewalReminderSent ProductEventType = "pro_renewal_reminder_sent"
	ProductEventFreeCapHit             ProductEventType = "free_cap_hit"
	ProductEventFreeHiddenByCap        ProductEventType = "free_hidden_by_cap"
)

// ProductEvent — минимальное событие продуктовой аналитики для SQL-дашбордов.
type ProductEvent struct {
	Type       ProductEventType
	UserID     int64
	JobID      int64
	Source     string
	Properties map[string]any
}

// ProductEventRepository сохраняет продуктовые события.
type ProductEventRepository interface {
	Record(ctx context.Context, event ProductEvent) error
	ExistsSince(ctx context.Context, userID int64, eventType ProductEventType, since time.Time, propertyKey, propertyValue string) (bool, error)
}
