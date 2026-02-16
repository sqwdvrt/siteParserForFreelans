package redis

import (
	"context"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	goredis "github.com/redis/go-redis/v9"
)

func TestNonceStore_Use(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	store := NewNonceStore(client, "test:nonce")
	ctx := context.Background()

	ok, err := store.Use(ctx, "tg:1:abc", 5*time.Minute)
	if err != nil {
		t.Fatalf("Use: %v", err)
	}
	if !ok {
		t.Fatal("first use must succeed")
	}

	ok, err = store.Use(ctx, "tg:1:abc", 5*time.Minute)
	if err != nil {
		t.Fatalf("Use second: %v", err)
	}
	if ok {
		t.Fatal("second use must fail for replayed nonce")
	}
}

func TestRateLimiter_Allow(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer client.Close()

	limiter := NewRateLimiter(client, "test:rl")
	ctx := context.Background()

	for i := 0; i < 3; i++ {
		ok, err := limiter.Allow(ctx, "ip:1.2.3.4", 3, time.Minute)
		if err != nil {
			t.Fatalf("Allow: %v", err)
		}
		if !ok {
			t.Fatalf("call %d should be allowed", i+1)
		}
	}

	ok, err := limiter.Allow(ctx, "ip:1.2.3.4", 3, time.Minute)
	if err != nil {
		t.Fatalf("Allow over limit: %v", err)
	}
	if ok {
		t.Fatal("over-limit call must be denied")
	}
}
