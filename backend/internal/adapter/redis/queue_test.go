package redis

import (
	"context"
	"encoding/json"
	"testing"

	goredis "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
)

func TestQueue_Enqueue(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	queue := NewQueue(client, "ai-process")
	ctx := context.Background()

	err := queue.Enqueue(ctx, 42)
	if err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	// Проверяем payload в очереди
	val, err := client.LPop(ctx, "ai-process").Result()
	if err != nil {
		t.Fatalf("LPop: %v", err)
	}
	var payload struct {
		JobID   int64  `json:"job_id"`
		TraceID string `json:"trace_id,omitempty"`
	}
	if err := json.Unmarshal([]byte(val), &payload); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if payload.JobID != 42 {
		t.Errorf("want job_id=42, got %d", payload.JobID)
	}
	if payload.TraceID != "" {
		t.Errorf("want empty trace_id by default, got %q", payload.TraceID)
	}
}

func TestQueue_Enqueue_PropagatesTraceIDFromContext(t *testing.T) {
	mr := mustRunMiniRedis(t)
	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	queue := NewQueue(client, "ai-process")
	ctx := observability.WithTraceID(context.Background(), "trace-123")
	if err := queue.Enqueue(ctx, 42); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}

	val, err := client.LPop(context.Background(), "ai-process").Result()
	if err != nil {
		t.Fatalf("LPop: %v", err)
	}
	var payload struct {
		JobID   int64  `json:"job_id"`
		TraceID string `json:"trace_id,omitempty"`
	}
	if err := json.Unmarshal([]byte(val), &payload); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if payload.TraceID != "trace-123" {
		t.Fatalf("trace_id=%q want=trace-123", payload.TraceID)
	}
}

func TestQueue_Enqueue_RejectsZero(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	queue := NewQueue(client, "ai-process")
	ctx := context.Background()

	err := queue.Enqueue(ctx, 0)
	if err == nil {
		t.Fatal("want error for job_id=0")
	}
}

func TestQueue_Enqueue_RejectsNegative(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	queue := NewQueue(client, "ai-process")
	ctx := context.Background()

	err := queue.Enqueue(ctx, -1)
	if err == nil {
		t.Fatal("want error for job_id=-1")
	}
}

func TestQueue_NewDefaultQueue(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	q := NewQueue(client, "")
	if q == nil {
		t.Fatal("NewQueue returned nil")
	}
	// Проверяем что использует default
	ctx := context.Background()
	if err := q.Enqueue(ctx, 1); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	val, _ := client.LPop(ctx, "ai-process").Result()
	if val == "" {
		t.Error("expected data in ai-process queue")
	}
}

func TestQueue_Ping(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	queue := NewQueue(client, "ai-process")
	ctx := context.Background()

	if err := queue.Ping(ctx); err != nil {
		t.Errorf("Ping: %v", err)
	}
}
