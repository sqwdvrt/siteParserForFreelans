package redis

import (
	"context"
	"encoding/json"
	"fmt"

	redis "github.com/redis/go-redis/v9"
)

const defaultQueue = "ai-process"

// Queue реализует port.JobQueue через Redis List.
type Queue struct {
	client *redis.Client
	queue  string
}

// NewQueue создаёт очередь.
func NewQueue(client *redis.Client, queueName string) *Queue {
	if queueName == "" {
		queueName = defaultQueue
	}
	return &Queue{client: client, queue: queueName}
}

// Enqueue добавляет job_id в очередь. Payload: {"job_id": N}.
// jobID должен быть > 0.
func (q *Queue) Enqueue(ctx context.Context, jobID int64) error {
	if jobID <= 0 {
		return fmt.Errorf("job_id must be positive, got %d", jobID)
	}
	payload, err := json.Marshal(map[string]int64{"job_id": jobID})
	if err != nil {
		return fmt.Errorf("marshal payload: %w", err)
	}
	return q.client.LPush(ctx, q.queue, payload).Err()
}

// Ping проверяет подключение к Redis.
func (q *Queue) Ping(ctx context.Context) error {
	return q.client.Ping(ctx).Err()
}
