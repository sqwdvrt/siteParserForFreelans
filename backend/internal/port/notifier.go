package port

import (
	"context"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// BatchNotifyItem — один элемент batch-уведомления.
type BatchNotifyItem struct {
	Job           *domain.Job
	WhyItFits     string
	Rank          int
	FinalScore    float64
	RankerVersion string
	ReasonCodes   []string
}

// NotifyPayload — данные для уведомления о проекте.
type NotifyPayload struct {
	Job           *domain.Job
	Score         float64
	FinalScore    float64
	RankerVersion string
	ReasonCodes   []string
	WhyItFits     string // 1–2 фразы из ai_metadata, почему подходит (опционально)
	Batch         []BatchNotifyItem
	CriticScore   float64
}

// Notifier отправляет уведомления пользователям (например, в Telegram).
type Notifier interface {
	Send(ctx context.Context, telegramID int64, p NotifyPayload) error
}
