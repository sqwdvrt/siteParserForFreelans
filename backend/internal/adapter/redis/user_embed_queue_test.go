package redis

import (
	"context"
	"encoding/json"
	"testing"

	redis "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
)

func TestUserEmbedQueue_Enqueue(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	q := NewUserEmbedQueue(client, "user-embed")
	ctx := context.Background()

	if err := q.Enqueue(ctx, 42); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if err := q.Enqueue(ctx, 100); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// BRPOP returns rightmost, so 100 first
	val, err := client.RPop(ctx, "user-embed").Result()
	if err != nil {
		t.Fatalf("RPop: %v", err)
	}
	var payload struct {
		UserID  int64  `json:"user_id"`
		TraceID string `json:"trace_id,omitempty"`
	}
	if err := json.Unmarshal([]byte(val), &payload); err != nil {
		t.Fatalf("unmarshal payload: %v", err)
	}
	if payload.UserID != 100 && payload.UserID != 42 {
		t.Errorf("unexpected payload: %s", val)
	}
	if payload.TraceID != "" {
		t.Errorf("expected empty trace_id by default, got %q", payload.TraceID)
	}
}

func TestUserEmbedQueue_NewDefaultQueue(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	q := NewUserEmbedQueue(client, "")
	if q == nil {
		t.Fatal("NewUserEmbedQueue returned nil")
	}
	ctx := context.Background()
	if err := q.Enqueue(ctx, 1); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	val, _ := client.LPop(ctx, "user-embed").Result()
	if val == "" {
		t.Error("expected data in user-embed queue")
	}
}

func TestUserEmbedQueue_EnqueueInvalid(t *testing.T) {
	mr := mustRunMiniRedis(t)
	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	q := NewUserEmbedQueue(client, "user-embed")
	ctx := context.Background()

	if err := q.Enqueue(ctx, 0); err == nil {
		t.Error("expected error for user_id=0")
	}
	if err := q.Enqueue(ctx, -1); err == nil {
		t.Error("expected error for user_id=-1")
	}
}

func TestUserEmbedQueue_Enqueue_PropagatesTraceIDFromContext(t *testing.T) {
	mr := mustRunMiniRedis(t)
	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	q := NewUserEmbedQueue(client, "user-embed")
	ctx := observability.WithTraceID(context.Background(), "trace-ue-1")
	if err := q.Enqueue(ctx, 7); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	val, err := client.RPop(context.Background(), "user-embed").Result()
	if err != nil {
		t.Fatalf("RPop: %v", err)
	}
	var payload struct {
		UserID  int64  `json:"user_id"`
		TraceID string `json:"trace_id,omitempty"`
	}
	if err := json.Unmarshal([]byte(val), &payload); err != nil {
		t.Fatalf("unmarshal payload: %v", err)
	}
	if payload.TraceID != "trace-ue-1" {
		t.Fatalf("trace_id=%q want=trace-ue-1", payload.TraceID)
	}
}
