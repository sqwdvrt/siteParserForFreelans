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
const defaultMaxNackRetries = 5
const DefaultMatchNotifyPopTimeout = 60 * time.Second

var requeueScript = redis.NewScript(`
local removed = redis.call("LREM", KEYS[1], 1, ARGV[1])
if removed > 0 then
  redis.call("RPUSH", KEYS[2], ARGV[2])
  return 1
end
return 0
`)

// MatchNotifyConsumer реализует port.MatchNotifyConsumer через Redis BRPOPLPUSH + processing queue.
type MatchNotifyConsumer struct {
	client          *redis.Client
	queue           string
	processingQueue string
	dlqQueue        string
	maxNackRetries  int
	popTimeout      time.Duration
}

type MatchNotifyConsumerOption func(*MatchNotifyConsumer)

func WithMatchNotifyPopTimeout(timeout time.Duration) MatchNotifyConsumerOption {
	return func(c *MatchNotifyConsumer) {
		if timeout > 0 {
			c.popTimeout = timeout
		}
	}
}

// NewMatchNotifyConsumer создаёт consumer.
func NewMatchNotifyConsumer(client *redis.Client, queueName string, opts ...MatchNotifyConsumerOption) *MatchNotifyConsumer {
	if queueName == "" {
		queueName = defaultMatchNotifyQueue
	}
	consumer := &MatchNotifyConsumer{
		client:          client,
		queue:           queueName,
		processingQueue: queueName + ":processing",
		dlqQueue:        queueName + ":dlq",
		maxNackRetries:  defaultMaxNackRetries,
		popTimeout:      DefaultMatchNotifyPopTimeout,
	}
	for _, opt := range opts {
		if opt != nil {
			opt(consumer)
		}
	}
	return consumer
}

// Recover переносит застрявшие сообщения из processing обратно в основную очередь.
func (c *MatchNotifyConsumer) Recover(ctx context.Context) error {
	for {
		_, err := c.client.RPopLPush(ctx, c.processingQueue, c.queue).Result()
		if err == nil {
			continue
		}
		if errors.Is(err, redis.Nil) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil
		}
		return err
	}
}

// Pop атомарно переносит сообщение в processing (BRPOPLPUSH) и возвращает delivery.
func (c *MatchNotifyConsumer) Pop(ctx context.Context) (*port.MatchNotifyMessage, error) {
	raw, err := c.client.BRPopLPush(ctx, c.queue, c.processingQueue, c.popTimeout).Result()
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
	if !isValidMatchNotifyPayload(p) {
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

// Nack отклоняет доставку: атомарно удаляет payload из processing и перекидывает
// в конец основной очереди, а при превышении лимита retry — в DLQ.
func (c *MatchNotifyConsumer) Nack(ctx context.Context, msg *port.MatchNotifyMessage) error {
	if msg == nil || msg.Receipt == "" {
		return nil
	}
	outRaw, toDLQ, err := c.prepareNackPayload(msg.Receipt)
	if err != nil {
		return err
	}
	targetQueue := c.queue
	if toDLQ {
		targetQueue = c.dlqQueue
	}
	_, err = requeueScript.Run(
		ctx,
		c.client,
		[]string{c.processingQueue, targetQueue},
		msg.Receipt,
		outRaw,
	).Int()
	return err
}

// Requeue возвращает delivery в основную очередь без изменения payload/retry-счётчика.
func (c *MatchNotifyConsumer) Requeue(ctx context.Context, msg *port.MatchNotifyMessage) error {
	if msg == nil || msg.Receipt == "" {
		return nil
	}
	_, err := requeueScript.Run(
		ctx,
		c.client,
		[]string{c.processingQueue, c.queue},
		msg.Receipt,
		msg.Receipt,
	).Int()
	return err
}

func (c *MatchNotifyConsumer) ackRaw(ctx context.Context, raw string) error {
	_, err := c.client.LRem(ctx, c.processingQueue, 1, raw).Result()
	return err
}

func (c *MatchNotifyConsumer) prepareNackPayload(raw string) (string, bool, error) {
	var payload map[string]interface{}
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		return raw, true, nil
	}
	if payload == nil {
		return raw, true, nil
	}
	retries := asInt(payload["_retry_count"])
	if retries < 0 {
		retries = 0
	}
	retries++
	payload["_retry_count"] = retries
	out, err := json.Marshal(payload)
	if err != nil {
		return "", false, err
	}
	return string(out), retries > c.maxNackRetries, nil
}

func asInt(v interface{}) int {
	switch x := v.(type) {
	case int:
		return x
	case int32:
		return int(x)
	case int64:
		return int(x)
	case float32:
		return int(x)
	case float64:
		return int(x)
	default:
		return 0
	}
}

func isValidMatchNotifyPayload(p port.MatchNotifyPayload) bool {
	if p.UserID <= 0 {
		return false
	}
	if p.JobID > 0 {
		return true
	}
	if len(p.Jobs) == 0 {
		return false
	}
	for _, item := range p.Jobs {
		if item.JobID <= 0 {
			return false
		}
	}
	return true
}
