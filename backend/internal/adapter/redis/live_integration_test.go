//go:build integration
// +build integration

package redis

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	goredis "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

func setupLiveRedis(t *testing.T) (*goredis.Client, string) {
	t.Helper()

	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		t.Fatal("REDIS_URL not set")
	}

	opts, err := goredis.ParseURL(redisURL)
	if err != nil {
		t.Fatalf("parse REDIS_URL: %v", err)
	}
	if strings.HasPrefix(opts.Addr, "localhost:") {
		opts.Addr = strings.Replace(opts.Addr, "localhost:", "127.0.0.1:", 1)
	}
	opts.DialTimeout = 1 * time.Second
	opts.ReadTimeout = 1 * time.Second
	opts.WriteTimeout = 1 * time.Second
	client := goredis.NewClient(opts)

	start := time.Now()
	var pingErr error
	for time.Since(start) < 10*time.Second {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		pingErr = client.Ping(ctx).Err()
		cancel()
		if pingErr == nil {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	if pingErr != nil {
		if errors.Is(pingErr, context.DeadlineExceeded) {
			t.Fatalf("redis ping timeout: %v", pingErr)
		}
		t.Fatalf("redis ping: %v", pingErr)
	}

	prefix := fmt.Sprintf("it:%d:%d", os.Getpid(), time.Now().UnixNano())
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cleanupCancel()

		var keys []string
		iter := client.Scan(cleanupCtx, 0, prefix+"*", 0).Iterator()
		for iter.Next(cleanupCtx) {
			keys = append(keys, iter.Val())
		}
		if err := iter.Err(); err != nil {
			t.Logf("scan cleanup: %v", err)
		}
		if len(keys) > 0 {
			if err := client.Del(cleanupCtx, keys...).Err(); err != nil {
				t.Logf("delete cleanup keys: %v", err)
			}
		}
		_ = client.Close()
	})

	return client, prefix
}

func TestMatchNotifyConsumer_LiveRedis_PopAck(t *testing.T) {
	client, prefix := setupLiveRedis(t)
	queueName := prefix + ":match-notify"

	payload := port.MatchNotifyPayload{UserID: 10, JobID: 20, MatchScore: 0.9}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	if err := client.LPush(context.Background(), queueName, string(raw)).Err(); err != nil {
		t.Fatalf("LPush: %v", err)
	}

	consumer := NewMatchNotifyConsumer(client, queueName)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg == nil {
		t.Fatal("Pop: want message, got nil")
	}
	if msg.Payload.UserID != payload.UserID || msg.Payload.JobID != payload.JobID {
		t.Fatalf("unexpected payload: %+v", msg.Payload)
	}
	if err := consumer.Ack(ctx, msg); err != nil {
		t.Fatalf("Ack: %v", err)
	}

	procLen, err := client.LLen(context.Background(), queueName+":processing").Result()
	if err != nil {
		t.Fatalf("LLen processing: %v", err)
	}
	if procLen != 0 {
		t.Fatalf("want processing queue empty after Ack, got %d", procLen)
	}
}

func TestMatchNotifyConsumer_LiveRedis_NackToDLQ(t *testing.T) {
	client, prefix := setupLiveRedis(t)
	queueName := prefix + ":match-notify"

	raw := `{"user_id":10,"job_id":20,"match_score":0.9,"_retry_count":5}`
	if err := client.LPush(context.Background(), queueName, raw).Err(); err != nil {
		t.Fatalf("LPush: %v", err)
	}

	consumer := NewMatchNotifyConsumer(client, queueName)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

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

	dlqRaw, err := client.LIndex(context.Background(), queueName+":dlq", 0).Result()
	if err != nil {
		t.Fatalf("LIndex dlq: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal([]byte(dlqRaw), &got); err != nil {
		t.Fatalf("unmarshal dlq payload: %v", err)
	}
	if got["_retry_count"] != float64(6) {
		t.Fatalf("want _retry_count=6, got %v", got["_retry_count"])
	}
}

func TestMatchNotifyConsumer_LiveRedis_Recover(t *testing.T) {
	client, prefix := setupLiveRedis(t)
	queueName := prefix + ":match-notify"

	if err := client.LPush(context.Background(), queueName+":processing", `{"user_id":1,"job_id":2,"match_score":0.5}`).Err(); err != nil {
		t.Fatalf("LPush processing: %v", err)
	}

	consumer := NewMatchNotifyConsumer(client, queueName)
	if err := consumer.Recover(context.Background()); err != nil {
		t.Fatalf("Recover: %v", err)
	}

	srcLen, err := client.LLen(context.Background(), queueName).Result()
	if err != nil {
		t.Fatalf("LLen source: %v", err)
	}
	if srcLen != 1 {
		t.Fatalf("want 1 message in source after Recover, got %d", srcLen)
	}
	procLen, err := client.LLen(context.Background(), queueName+":processing").Result()
	if err != nil {
		t.Fatalf("LLen processing: %v", err)
	}
	if procLen != 0 {
		t.Fatalf("want empty processing after Recover, got %d", procLen)
	}
}
