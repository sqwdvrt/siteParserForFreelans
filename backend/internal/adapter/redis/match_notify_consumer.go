package redis

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const defaultMatchNotifyQueue = "match-notify"
const maxRecoverMessages = 1000

var nackScript = redis.NewScript(`
local removed = redis.call("LREM", KEYS[1], 1, ARGV[1])
if removed > 0 then
  redis.call("LPUSH", KEYS[2], ARGV[1])
  return 1
end
return 0
`)

// MatchNotifyConsumer реализует port.MatchNotifyConsumer через Redis BRPOPLPUSH + processing queue.
type MatchNotifyConsumer struct {
	client          *redis.Client
	queue           string
	processingQueue string
}

// NewMatchNotifyConsumer создаёт consumer.
func NewMatchNotifyConsumer(client *redis.Client, queueName string) *MatchNotifyConsumer {
	if queueName == "" {
		queueName = defaultMatchNotifyQueue
	}
	return &MatchNotifyConsumer{
		client:          client,
		queue:           queueName,
		processingQueue: queueName + ":processing",
	}
}

// Recover переносит застрявшие сообщения из processing обратно в основную очередь.
func (c *MatchNotifyConsumer) Recover(ctx context.Context) error {
	for i := 0; i < maxRecoverMessages; i++ {
		_, err := c.client.RPopLPush(ctx, c.processingQueue, c.queue).Result()
		if err == nil {
			continue
		}
		if errors.Is(err, redis.Nil) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil
		}
		return err
	}
	return fmt.Errorf("recover limit reached (%d messages), possible processing queue overflow", maxRecoverMessages)
}

// Pop атомарно переносит сообщение в processing (BRPOPLPUSH) и возвращает delivery.
func (c *MatchNotifyConsumer) Pop(ctx context.Context) (*port.MatchNotifyMessage, error) {
	raw, err := c.client.BRPopLPush(ctx, c.queue, c.processingQueue, 5*time.Second).Result()
	if err != nil {
		if errors.Is(err, redis.Nil) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil, nil
		}
		return nil, err
	}
	if raw == "" {
		return nil, nil
	}
	var p port.MatchNotifyPayload
	if err := json.Unmarshal([]byte(raw), &p); err != nil {
		_ = c.ackRaw(ctx, raw) // poison payload: remove from processing
		return nil, err
	}
	if p.UserID <= 0 || p.JobID <= 0 {
		_ = c.ackRaw(ctx, raw) // invalid payload: discard
		return nil, nil        // skip invalid payload
	}
	return &port.MatchNotifyMessage{
		Payload: p,
		Receipt: raw,
	}, nil
}

// Ack подтверждает доставку: удаляет payload из processing.
func (c *MatchNotifyConsumer) Ack(ctx context.Context, msg *port.MatchNotifyMessage) error {
	if msg == nil || msg.Receipt == "" {
		return nil
	}
	return c.ackRaw(ctx, msg.Receipt)
}

// Nack отклоняет доставку: атомарно удаляет payload из processing и возвращает в основную очередь.
func (c *MatchNotifyConsumer) Nack(ctx context.Context, msg *port.MatchNotifyMessage) error {
	if msg == nil || msg.Receipt == "" {
		return nil
	}
	_, err := nackScript.Run(ctx, c.client, []string{c.processingQueue, c.queue}, msg.Receipt).Int()
	return err
}

func (c *MatchNotifyConsumer) ackRaw(ctx context.Context, raw string) error {
	_, err := c.client.LRem(ctx, c.processingQueue, 1, raw).Result()
	return err
}
