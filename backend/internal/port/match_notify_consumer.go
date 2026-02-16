package port

import "context"

// MatchNotifyPayload — сообщение из очереди match-notify.
type MatchNotifyPayload struct {
	UserID     int64   `json:"user_id"`
	JobID      int64   `json:"job_id"`
	MatchScore float64 `json:"match_score"`
}

// MatchNotifyConsumer — consumer очереди match-notify (BRPOP).
type MatchNotifyConsumer interface {
	// Pop блокирует до получения сообщения или отмены ctx. nil при shutdown.
	Pop(ctx context.Context) (*MatchNotifyPayload, error)
}
