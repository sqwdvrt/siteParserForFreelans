package redis

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	redis "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

func TestMatchNotifyConsumer_NewDefaultQueue(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	c := NewMatchNotifyConsumer(redis.NewClient(&redis.Options{Addr: mr.Addr()}), "")
	if c == nil {
		t.Fatal("NewMatchNotifyConsumer returned nil")
	}
	if c.queue != "match-notify" {
		t.Errorf("want default queue match-notify, got %q", c.queue)
	}
}

func TestMatchNotifyConsumer_NewCustomQueue(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	c := NewMatchNotifyConsumer(redis.NewClient(&redis.Options{Addr: mr.Addr()}), "custom-queue")
	if c.queue != "custom-queue" {
		t.Errorf("want custom-queue, got %q", c.queue)
	}
}

func TestMatchNotifyConsumer_Pop(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	payload := port.MatchNotifyPayload{UserID: 10, JobID: 20, MatchScore: 0.9}
	b, _ := json.Marshal(payload)
	client.LPush(context.Background(), "match-notify", string(b))

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	p, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if p == nil {
		t.Fatal("Pop: want payload, got nil")
	}
	if p.UserID != 10 || p.JobID != 20 || p.MatchScore != 0.9 {
		t.Errorf("Pop: got %+v", p)
	}
}

func TestMatchNotifyConsumer_Pop_InvalidPayload_Skip(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	// user_id=0 — invalid, должен вернуть nil
	client.LPush(context.Background(), "match-notify", `{"user_id":0,"job_id":1,"match_score":0.5}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	p, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if p != nil {
		t.Errorf("Pop: want nil for invalid payload (user_id=0), got %+v", p)
	}
}

func TestMatchNotifyConsumer_Pop_InvalidJSON(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	client.LPush(context.Background(), "match-notify", `{invalid json}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	p, err := consumer.Pop(ctx)
	if err == nil {
		t.Error("Pop: want error for invalid JSON")
	}
	if p != nil {
		t.Errorf("Pop: want nil on error, got %+v", p)
	}
}

func TestMatchNotifyConsumer_Pop_EmptyQueue_Timeout(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("miniredis: %v", err)
	}
	defer mr.Close()

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer client.Close()

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	p, err := consumer.Pop(ctx)
	// На пустой очереди: nil,nil (context) или nil,redis.Nil (timeout)
	if err != nil && err != redis.Nil {
		t.Fatalf("Pop: %v", err)
	}
	if p != nil {
		t.Errorf("Pop on empty queue: want nil, got %+v", p)
	}
}
