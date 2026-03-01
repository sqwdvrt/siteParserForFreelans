package redis

import (
	"context"
	"encoding/json"
	"fmt"

	redis "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
)

const defaultUserEmbedQueue = "user-embed"

// UserEmbedQueue реализует port.UserEmbedQueue через Redis List.
type UserEmbedQueue struct {
	client *redis.Client
	queue  string
}

// NewUserEmbedQueue создаёт очередь user-embed.
func NewUserEmbedQueue(client *redis.Client, queueName string) *UserEmbedQueue {
	if queueName == "" {
		queueName = defaultUserEmbedQueue
	}
	return &UserEmbedQueue{client: client, queue: queueName}
}

// Enqueue добавляет user_id в очередь. Payload: {"user_id": N}.
func (q *UserEmbedQueue) Enqueue(ctx context.Context, userID int64) error {
	if userID <= 0 {
		return fmt.Errorf("user_id must be positive, got %d", userID)
	}
	payloadData := struct {
		UserID  int64  `json:"user_id"`
		TraceID string `json:"trace_id,omitempty"`
	}{
		UserID:  userID,
		TraceID: observability.TraceIDFromContext(ctx),
	}
	payload, err := json.Marshal(payloadData)
	if err != nil {
		return fmt.Errorf("marshal payload: %w", err)
	}
	return q.client.LPush(ctx, q.queue, payload).Err()
}
