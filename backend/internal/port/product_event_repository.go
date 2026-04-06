package port

import "context"

type ProductEventType string

const (
	ProductEventUserRegistered     ProductEventType = "user_registered"
	ProductEventProfileUpdated     ProductEventType = "profile_updated"
	ProductEventProfileCompleted   ProductEventType = "profile_completed"
	ProductEventPreferencesUpdated ProductEventType = "preferences_updated"
	ProductEventNotifyHourUpdated  ProductEventType = "notify_hour_updated"
	ProductEventPauseUpdated       ProductEventType = "pause_updated"
	ProductEventFeedbackSubmitted  ProductEventType = "feedback_submitted"
	ProductEventNotificationSent   ProductEventType = "notification_sent"
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
}
