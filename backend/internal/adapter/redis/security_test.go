package redis

import (
	"context"
	"testing"
	"time"

	goredis "github.com/redis/go-redis/v9"
)

func TestNonceStore_Use(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

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
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

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

func TestRedisLock_AcquireRelease(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

	lock := NewRedisLock(client)
	ctx := context.Background()

	lease, ok, err := lock.Acquire(ctx, "test:lock:digest", 5*time.Minute)
	if err != nil {
		t.Fatalf("Acquire: %v", err)
	}
	if !ok || lease == nil {
		t.Fatal("first acquire must succeed")
	}

	secondLease, secondOK, err := lock.Acquire(ctx, "test:lock:digest", 5*time.Minute)
	if err != nil {
		t.Fatalf("Acquire second: %v", err)
	}
	if secondOK || secondLease != nil {
		t.Fatal("second acquire must fail while lock is held")
	}

	released, err := lease.Release(ctx)
	if err != nil {
		t.Fatalf("Release: %v", err)
	}
	if !released {
		t.Fatal("release must delete owned lock")
	}

	thirdLease, thirdOK, err := lock.Acquire(ctx, "test:lock:digest", 5*time.Minute)
	if err != nil {
		t.Fatalf("Acquire after release: %v", err)
	}
	if !thirdOK || thirdLease == nil {
		t.Fatal("acquire after release must succeed")
	}
}

func TestRedisLock_ReleaseRejectsMismatchedToken(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

	lock := NewRedisLock(client)
	ctx := context.Background()

	lease, ok, err := lock.Acquire(ctx, "test:lock:token", 5*time.Minute)
	if err != nil {
		t.Fatalf("Acquire: %v", err)
	}
	if !ok || lease == nil {
		t.Fatal("acquire must succeed")
	}

	if err := client.Set(ctx, "test:lock:token", "other-token", 0).Err(); err != nil {
		t.Fatalf("overwrite lock token: %v", err)
	}

	released, err := lease.Release(ctx)
	if err != nil {
		t.Fatalf("Release: %v", err)
	}
	if released {
		t.Fatal("release must not delete lock owned by another token")
	}

	got, err := client.Get(ctx, "test:lock:token").Result()
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got != "other-token" {
		t.Fatalf("lock token = %q, want other-token", got)
	}
}

func TestRedisLock_Expires(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := goredis.NewClient(&goredis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

	lock := NewRedisLock(client)
	ctx := context.Background()

	lease, ok, err := lock.Acquire(ctx, "test:lock:ttl", time.Second)
	if err != nil {
		t.Fatalf("Acquire: %v", err)
	}
	if !ok || lease == nil {
		t.Fatal("acquire must succeed")
	}

	mr.FastForward(1500 * time.Millisecond)

	exists, err := client.Exists(ctx, "test:lock:ttl").Result()
	if err != nil {
		t.Fatalf("Exists after TTL: %v", err)
	}
	if exists != 0 {
		t.Fatal("lock key must expire after TTL")
	}
}
