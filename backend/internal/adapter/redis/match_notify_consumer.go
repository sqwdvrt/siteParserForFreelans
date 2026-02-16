package redis

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const defaultMatchNotifyQueue = "match-notify"

// MatchNotifyConsumer реализует port.MatchNotifyConsumer через Redis BRPOP.
type MatchNotifyConsumer struct {
	client *redis.Client
	queue  string
}

// NewMatchNotifyConsumer создаёт consumer.
func NewMatchNotifyConsumer(client *redis.Client, queueName string) *MatchNotifyConsumer {
	if queueName == "" {
		queueName = defaultMatchNotifyQueue
	}
	return &MatchNotifyConsumer{client: client, queue: queueName}
}

// Pop блокирует до получения сообщения (BRPOP, timeout 5 сек) или отмены ctx.
func (c *MatchNotifyConsumer) Pop(ctx context.Context) (*port.MatchNotifyPayload, error) {
	result, err := c.client.BRPop(ctx, 5*time.Second, c.queue).Result()
	if err != nil {
		if errors.Is(err, redis.Nil) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil, nil
		}
		return nil, err
	}
	if len(result) < 2 {
		return nil, nil
	}
	var p port.MatchNotifyPayload
	if err := json.Unmarshal([]byte(result[1]), &p); err != nil {
		return nil, err
	}
	if p.UserID <= 0 || p.JobID <= 0 {
		return nil, nil // skip invalid payload
	}
	return &p, nil
}
