package redis

import (
	"context"
	"encoding/json"
	"fmt"

	redis "github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
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
	carrier := MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	payloadData := struct {
		JobID       int64  `json:"job_id"`
		TraceID     string `json:"trace_id,omitempty"`
		Traceparent string `json:"traceparent,omitempty"`
	}{
		JobID:       jobID,
		TraceID:     observability.TraceIDFromContext(ctx),
		Traceparent: carrier["traceparent"],
	}
	payload, err := json.Marshal(payloadData)
	if err != nil {
		return fmt.Errorf("marshal payload: %w", err)
	}
	return q.client.LPush(ctx, q.queue, payload).Err()
}

// Ping проверяет подключение к Redis.
func (q *Queue) Ping(ctx context.Context) error {
	return q.client.Ping(ctx).Err()
}
