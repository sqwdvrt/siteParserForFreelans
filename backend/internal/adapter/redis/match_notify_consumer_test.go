package redis

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	redis "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

func TestMatchNotifyConsumer_NewDefaultQueue(t *testing.T) {
	mr := mustRunMiniRedis(t)

	c := NewMatchNotifyConsumer(redis.NewClient(&redis.Options{Addr: mr.Addr()}), "")
	if c == nil {
		t.Fatal("NewMatchNotifyConsumer returned nil")
	}
	if c.queue != "match-notify" {
		t.Errorf("want default queue match-notify, got %q", c.queue)
	}
}

func TestMatchNotifyConsumer_NewCustomQueue(t *testing.T) {
	mr := mustRunMiniRedis(t)

	c := NewMatchNotifyConsumer(redis.NewClient(&redis.Options{Addr: mr.Addr()}), "custom-queue")
	if c.queue != "custom-queue" {
		t.Errorf("want custom-queue, got %q", c.queue)
	}
}

func TestMatchNotifyConsumer_Pop(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	payload := port.MatchNotifyPayload{UserID: 10, JobID: 20, MatchScore: 0.9}
	b, _ := json.Marshal(payload)
	client.LPush(context.Background(), "match-notify", string(b))

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg == nil {
		t.Fatal("Pop: want payload, got nil")
	}
	p := msg.Payload
	if p.UserID != 10 || p.JobID != 20 || p.MatchScore != 0.9 {
		t.Errorf("Pop: got %+v", p)
	}
	if err := consumer.Ack(ctx, msg); err != nil {
		t.Fatalf("Ack: %v", err)
	}
}

func TestMatchNotifyConsumer_Pop_InvalidPayload_Skip(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	// user_id=0 — invalid, должен вернуть nil
	client.LPush(context.Background(), "match-notify", `{"user_id":0,"job_id":1,"match_score":0.5}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg != nil {
		t.Errorf("Pop: want nil for invalid payload (user_id=0), got %+v", msg)
	}
}

func TestMatchNotifyConsumer_Pop_InvalidJSON(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	client.LPush(context.Background(), "match-notify", `{invalid json}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg, err := consumer.Pop(ctx)
	if err == nil {
		t.Error("Pop: want error for invalid JSON")
	}
	if msg != nil {
		t.Errorf("Pop: want nil on error, got %+v", msg)
	}
}

func TestMatchNotifyConsumer_Pop_EmptyQueue_Timeout(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg != nil {
		t.Errorf("Pop on empty queue: want nil, got %+v", msg)
	}
}

func TestMatchNotifyConsumer_Nack_RequeuesMessage(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	payload := port.MatchNotifyPayload{UserID: 10, JobID: 20, MatchScore: 0.9}
	b, _ := json.Marshal(payload)
	client.LPush(context.Background(), "match-notify", string(b))

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg == nil {
		t.Fatal("Pop: want message, got nil")
	}
	if err := consumer.Nack(ctx, msg); err != nil {
		t.Fatalf("Nack: %v", err)
	}

	got, err := client.LLen(ctx, "match-notify").Result()
	if err != nil {
		t.Fatalf("LLen queue: %v", err)
	}
	if got != 1 {
		t.Fatalf("expected 1 message in source queue after nack, got %d", got)
	}
	gotProcessing, err := client.LLen(ctx, "match-notify:processing").Result()
	if err != nil {
		t.Fatalf("LLen processing: %v", err)
	}
	if gotProcessing != 0 {
		t.Fatalf("expected empty processing queue after nack, got %d", gotProcessing)
	}

	raw, err := client.LIndex(ctx, "match-notify", 0).Result()
	if err != nil {
		t.Fatalf("LIndex queue: %v", err)
	}
	var gotPayload map[string]interface{}
	if err := json.Unmarshal([]byte(raw), &gotPayload); err != nil {
		t.Fatalf("json unmarshal requeued payload: %v", err)
	}
	if gotPayload["_retry_count"] != float64(1) {
		t.Fatalf("expected _retry_count=1 in requeued payload, got %v", gotPayload["_retry_count"])
	}
}

func TestMatchNotifyConsumer_Requeue_DoesNotIncreaseRetryCount(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	client.LPush(context.Background(), "match-notify", `{"user_id":10,"job_id":20,"match_score":0.9,"_retry_count":5}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg == nil {
		t.Fatal("Pop: want message, got nil")
	}
	if err := consumer.Requeue(ctx, msg); err != nil {
		t.Fatalf("Requeue: %v", err)
	}

	got, err := client.LLen(ctx, "match-notify").Result()
	if err != nil {
		t.Fatalf("LLen queue: %v", err)
	}
	if got != 1 {
		t.Fatalf("expected 1 message in source queue after requeue, got %d", got)
	}
	gotProcessing, err := client.LLen(ctx, "match-notify:processing").Result()
	if err != nil {
		t.Fatalf("LLen processing: %v", err)
	}
	if gotProcessing != 0 {
		t.Fatalf("expected empty processing queue after requeue, got %d", gotProcessing)
	}

	raw, err := client.LIndex(ctx, "match-notify", 0).Result()
	if err != nil {
		t.Fatalf("LIndex queue: %v", err)
	}
	var gotPayload map[string]interface{}
	if err := json.Unmarshal([]byte(raw), &gotPayload); err != nil {
		t.Fatalf("json unmarshal requeued payload: %v", err)
	}
	if gotPayload["_retry_count"] != float64(5) {
		t.Fatalf("expected _retry_count to stay 5, got %v", gotPayload["_retry_count"])
	}
}

func TestMatchNotifyConsumer_Recover_MovesProcessingToSource(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	client.LPush(context.Background(), "match-notify:processing", `{"user_id":1,"job_id":2,"match_score":0.5}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	if err := consumer.Recover(ctx); err != nil {
		t.Fatalf("Recover: %v", err)
	}

	sourceLen, err := client.LLen(ctx, "match-notify").Result()
	if err != nil {
		t.Fatalf("LLen queue: %v", err)
	}
	if sourceLen != 1 {
		t.Fatalf("expected 1 message in source queue after recover, got %d", sourceLen)
	}
	procLen, err := client.LLen(ctx, "match-notify:processing").Result()
	if err != nil {
		t.Fatalf("LLen processing: %v", err)
	}
	if procLen != 0 {
		t.Fatalf("expected empty processing queue after recover, got %d", procLen)
	}
}

func TestMatchNotifyConsumer_Recover_MovesMoreThanThousandMessages(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	ctx := context.Background()
	const n = 1205
	for i := 0; i < n; i++ {
		client.LPush(ctx, "match-notify:processing", `{"user_id":1,"job_id":2,"match_score":0.5}`)
	}

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	if err := consumer.Recover(ctx); err != nil {
		t.Fatalf("Recover: %v", err)
	}

	sourceLen, err := client.LLen(ctx, "match-notify").Result()
	if err != nil {
		t.Fatalf("LLen queue: %v", err)
	}
	if sourceLen != n {
		t.Fatalf("expected %d messages in source queue after recover, got %d", n, sourceLen)
	}
	procLen, err := client.LLen(ctx, "match-notify:processing").Result()
	if err != nil {
		t.Fatalf("LLen processing: %v", err)
	}
	if procLen != 0 {
		t.Fatalf("expected empty processing queue after recover, got %d", procLen)
	}
}

func TestMatchNotifyConsumer_Nack_MovesToDLQAfterRetryLimit(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	client.LPush(context.Background(), "match-notify", `{"user_id":10,"job_id":20,"match_score":0.9,"_retry_count":5}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg == nil {
		t.Fatal("Pop: want message, got nil")
	}
	if err := consumer.Nack(ctx, msg); err != nil {
		t.Fatalf("Nack: %v", err)
	}

	sourceLen, err := client.LLen(ctx, "match-notify").Result()
	if err != nil {
		t.Fatalf("LLen queue: %v", err)
	}
	if sourceLen != 0 {
		t.Fatalf("expected source queue empty after DLQ move, got %d", sourceLen)
	}
	dlqLen, err := client.LLen(ctx, "match-notify:dlq").Result()
	if err != nil {
		t.Fatalf("LLen dlq: %v", err)
	}
	if dlqLen != 1 {
		t.Fatalf("expected 1 message in dlq after nack limit, got %d", dlqLen)
	}
}
