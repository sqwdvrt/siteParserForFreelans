package redis

import (
	"context"
	"encoding/json"
	"testing"

	goredis "github.com/redis/go-redis/v9"
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
		JobID int64 `json:"job_id"`
	}
	if err := json.Unmarshal([]byte(val), &payload); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if payload.JobID != 42 {
		t.Errorf("want job_id=42, got %d", payload.JobID)
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
