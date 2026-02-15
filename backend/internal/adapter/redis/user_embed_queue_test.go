package redis

import (
	"context"
	"testing"

	"github.com/alicebob/miniredis/v2"
	redis "github.com/redis/go-redis/v9"
)

func TestUserEmbedQueue_Enqueue(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

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

func TestUserEmbedQueue_EnqueueInvalid(t *testing.T) {
	mr, _ := miniredis.Run()
	defer mr.Close()
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
