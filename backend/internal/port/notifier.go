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
	BatchScore    float64
	CriticScore   float64
}

// EffectiveBatchScore returns the canonical batch score and falls back to the
// legacy CriticScore during the transition.
func (p NotifyPayload) EffectiveBatchScore() float64 {
	if p.BatchScore != 0 || p.CriticScore == 0 {
		return p.BatchScore
	}
	return p.CriticScore
}

// Notifier отправляет уведомления пользователям (например, в Telegram).
type Notifier interface {
	Send(ctx context.Context, telegramID int64, p NotifyPayload) error
}
