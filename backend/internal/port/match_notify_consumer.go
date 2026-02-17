package port

import "context"

// MatchNotifyPayload — сообщение из очереди match-notify.
type MatchNotifyPayload struct {
	UserID     int64   `json:"user_id"`
	JobID      int64   `json:"job_id"`
	MatchScore float64 `json:"match_score"`
}

// MatchNotifyMessage — доставленное сообщение из match-notify.
// Receipt — внутренний маркер доставки (raw payload) для Ack/Nack.
type MatchNotifyMessage struct {
	Payload MatchNotifyPayload
	Receipt string
}

// MatchNotifyConsumer — надёжный consumer очереди match-notify.
type MatchNotifyConsumer interface {
	// Recover возвращает сообщения, застрявшие в processing-очереди после рестарта.
	Recover(ctx context.Context) error
	// Pop атомарно переносит сообщение из основной очереди в processing и возвращает delivery.
	Pop(ctx context.Context) (*MatchNotifyMessage, error)
	// Ack подтверждает успешную обработку delivery (удаляет его из processing).
	Ack(ctx context.Context, msg *MatchNotifyMessage) error
	// Nack отклоняет delivery и возвращает его в основную очередь для повторной обработки.
	Nack(ctx context.Context, msg *MatchNotifyMessage) error
}
