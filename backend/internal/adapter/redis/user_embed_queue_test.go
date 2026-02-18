package redis

import (
	"context"
	"testing"

	redis "github.com/redis/go-redis/v9"
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
	if val != `{"user_id":100}` && val != `{"user_id":42}` {
		t.Errorf("unexpected payload: %s", val)
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
