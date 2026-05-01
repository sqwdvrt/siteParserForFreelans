package redis

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
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
	if c.popTimeout != DefaultMatchNotifyPopTimeout {
		t.Errorf("want default pop timeout %v, got %v", DefaultMatchNotifyPopTimeout, c.popTimeout)
	}
}

func TestMatchNotifyConsumer_NewCustomQueue(t *testing.T) {
	mr := mustRunMiniRedis(t)

	customTimeout := 3 * time.Second
	c := NewMatchNotifyConsumer(
		redis.NewClient(&redis.Options{Addr: mr.Addr()}),
		"custom-queue",
		WithMatchNotifyPopTimeout(customTimeout),
	)
	if c.queue != "custom-queue" {
		t.Errorf("want custom-queue, got %q", c.queue)
	}
	if c.popTimeout != customTimeout {
		t.Errorf("want custom pop timeout %v, got %v", customTimeout, c.popTimeout)
	}
}

func TestMatchNotifyConsumer_NewInvalidPopTimeout_UsesDefault(t *testing.T) {
	mr := mustRunMiniRedis(t)

	c := NewMatchNotifyConsumer(
		redis.NewClient(&redis.Options{Addr: mr.Addr()}),
		"custom-queue",
		WithMatchNotifyPopTimeout(0),
	)
	if c.popTimeout != DefaultMatchNotifyPopTimeout {
		t.Errorf("want default pop timeout %v, got %v", DefaultMatchNotifyPopTimeout, c.popTimeout)
	}
}

func TestMatchNotifyConsumer_Pop(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

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

func TestMatchNotifyConsumer_Pop_InvalidPayload_RoutesToDLQ(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

	// user_id=0 — invalid, должен быть роутнут в DLQ
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

	dlqLen, err := client.LLen(context.Background(), "match-notify:dlq").Result()
	if err != nil {
		t.Fatalf("LLen dlq: %v", err)
	}
	if dlqLen != 1 {
		t.Fatalf("expected 1 message in DLQ for invalid payload, got %d", dlqLen)
	}
}

func TestMatchNotifyConsumer_Pop_BatchPayload(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

	client.LPush(
		context.Background(),
		"match-notify",
		`{"user_id":10,"source":"ac","critic_score":8.2,"jobs":[{"job_id":101,"title":"Backend","why_it_fits":"Django","rank":1}]}`,
	)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg == nil {
		t.Fatal("Pop: want payload, got nil")
	}
	if msg.Payload.UserID != 10 {
		t.Fatalf("want user_id=10, got %d", msg.Payload.UserID)
	}
	if len(msg.Payload.Jobs) != 1 {
		t.Fatalf("want jobs len=1, got %d", len(msg.Payload.Jobs))
	}
	if msg.Payload.Jobs[0].JobID != 101 {
		t.Fatalf("want jobs[0].job_id=101, got %d", msg.Payload.Jobs[0].JobID)
	}
	if msg.Payload.Source != "ac" {
		t.Fatalf("want source=ac, got %q", msg.Payload.Source)
	}
}

func TestMatchNotifyConsumer_Pop_InvalidBatchPayload_RoutesToDLQ(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

	// batch с невалидным job_id — должен быть роутнут в DLQ
	client.LPush(context.Background(), "match-notify", `{"user_id":10,"jobs":[{"job_id":0,"rank":1}]}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: %v", err)
	}
	if msg != nil {
		t.Fatalf("Pop: want nil for invalid batch payload, got %+v", msg)
	}

	dlqLen, err := client.LLen(context.Background(), "match-notify:dlq").Result()
	if err != nil {
		t.Fatalf("LLen dlq: %v", err)
	}
	if dlqLen != 1 {
		t.Fatalf("expected 1 message in DLQ for invalid batch payload, got %d", dlqLen)
	}
}

func TestMatchNotifyConsumer_Pop_InvalidJSON_RoutesToDLQ(t *testing.T) {
	client := newFakeMatchNotifyRedisClient()
	client.LPush(context.Background(), "match-notify", `{invalid json}`)

	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg, err := consumer.Pop(ctx)
	if err != nil {
		t.Fatalf("Pop: want nil error for invalid JSON quarantine, got %v", err)
	}
	if msg != nil {
		t.Errorf("Pop: want nil for invalid JSON quarantine, got %+v", msg)
	}

	dlqLen, err2 := client.LLen(ctx, "match-notify:dlq").Result()
	if err2 != nil {
		t.Fatalf("LLen dlq: %v", err2)
	}
	if dlqLen != 1 {
		t.Fatalf("expected 1 message in DLQ for invalid JSON, got %d", dlqLen)
	}
	procLen, err := client.LLen(ctx, "match-notify:processing").Result()
	if err != nil {
		t.Fatalf("LLen processing: %v", err)
	}
	if procLen != 0 {
		t.Fatalf("expected empty processing queue after invalid JSON quarantine, got %d", procLen)
	}
}

func TestMatchNotifyConsumer_Pop_EmptyQueue_Timeout(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

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
	defer func() { _ = client.Close() }()

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
	if nackErr := consumer.Nack(ctx, msg); nackErr != nil {
		t.Fatalf("Nack: %v", nackErr)
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

func TestMatchNotifyConsumer_Nack_ReturnsErrorWhenDLQMoveMissing(t *testing.T) {
	client := newFakeMatchNotifyRedisClient()
	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg := &port.MatchNotifyMessage{
		Payload: port.MatchNotifyPayload{UserID: 10, JobID: 20, MatchScore: 0.9},
		Receipt: `{"user_id":10,"job_id":20,"match_score":0.9,"_retry_count":5}`,
	}

	if err := consumer.Nack(ctx, msg); err == nil {
		t.Fatal("Nack: want error when DLQ move cannot remove message from processing")
	}
}

func TestMatchNotifyConsumer_Requeue_ReturnsErrorWhenMessageMissing(t *testing.T) {
	client := newFakeMatchNotifyRedisClient()
	consumer := NewMatchNotifyConsumer(client, "match-notify")
	ctx := context.Background()

	msg := &port.MatchNotifyMessage{
		Payload: port.MatchNotifyPayload{UserID: 10, JobID: 20, MatchScore: 0.9},
		Receipt: `{"user_id":10,"job_id":20,"match_score":0.9}`,
	}

	if err := consumer.Requeue(ctx, msg); err == nil {
		t.Fatal("Requeue: want error when message cannot be removed from processing")
	}
}

type fakeMatchNotifyRedisClient struct {
	mu    sync.Mutex
	lists map[string][]string
}

func newFakeMatchNotifyRedisClient() *fakeMatchNotifyRedisClient {
	return &fakeMatchNotifyRedisClient{lists: make(map[string][]string)}
}

func (f *fakeMatchNotifyRedisClient) BRPopLPush(ctx context.Context, source string, destination string, timeout time.Duration) *redis.StringCmd {
	f.mu.Lock()
	defer f.mu.Unlock()

	src := f.lists[source]
	if len(src) == 0 {
		return redis.NewStringResult("", redis.Nil)
	}
	val := src[len(src)-1]
	f.lists[source] = src[:len(src)-1]
	f.lists[destination] = append([]string{val}, f.lists[destination]...)
	return redis.NewStringResult(val, nil)
}

func (f *fakeMatchNotifyRedisClient) RPopLPush(ctx context.Context, source string, destination string) *redis.StringCmd {
	return f.BRPopLPush(ctx, source, destination, 0)
}

func (f *fakeMatchNotifyRedisClient) LRem(ctx context.Context, key string, count int64, value interface{}) *redis.IntCmd {
	f.mu.Lock()
	defer f.mu.Unlock()

	items := f.lists[key]
	target := fmt.Sprint(value)
	removed := int64(0)
	for i, item := range items {
		if item == target {
			f.lists[key] = append(items[:i], items[i+1:]...)
			removed = 1
			break
		}
	}
	return redis.NewIntResult(removed, nil)
}

func (f *fakeMatchNotifyRedisClient) LPush(ctx context.Context, key string, values ...interface{}) *redis.IntCmd {
	f.mu.Lock()
	defer f.mu.Unlock()

	for i := len(values) - 1; i >= 0; i-- {
		f.lists[key] = append([]string{fmt.Sprint(values[i])}, f.lists[key]...)
	}
	return redis.NewIntResult(int64(len(f.lists[key])), nil)
}

func (f *fakeMatchNotifyRedisClient) LLen(ctx context.Context, key string) *redis.IntCmd {
	f.mu.Lock()
	defer f.mu.Unlock()

	return redis.NewIntResult(int64(len(f.lists[key])), nil)
}

func (f *fakeMatchNotifyRedisClient) LIndex(ctx context.Context, key string, index int64) *redis.StringCmd {
	f.mu.Lock()
	defer f.mu.Unlock()

	items := f.lists[key]
	if index < 0 || index >= int64(len(items)) {
		return redis.NewStringResult("", redis.Nil)
	}
	return redis.NewStringResult(items[index], nil)
}

func (f *fakeMatchNotifyRedisClient) Eval(ctx context.Context, script string, keys []string, args ...interface{}) *redis.Cmd {
	f.mu.Lock()
	defer f.mu.Unlock()

	cmd := redis.NewCmd(ctx)
	if len(keys) != 2 || len(args) != 2 {
		cmd.SetErr(fmt.Errorf("unexpected script arguments"))
		return cmd
	}
	receipt := fmt.Sprint(args[0])
	outRaw := fmt.Sprint(args[1])
	items := f.lists[keys[0]]
	removed := false
	for i, item := range items {
		if item == receipt {
			f.lists[keys[0]] = append(items[:i], items[i+1:]...)
			removed = true
			break
		}
	}
	if removed {
		f.lists[keys[1]] = append([]string{outRaw}, f.lists[keys[1]]...)
		cmd.SetVal(int64(1))
		return cmd
	}
	cmd.SetVal(int64(0))
	return cmd
}

func (f *fakeMatchNotifyRedisClient) EvalSha(ctx context.Context, sha1 string, keys []string, args ...interface{}) *redis.Cmd {
	cmd := redis.NewCmd(ctx)
	cmd.SetErr(fakeRedisError("NOSCRIPT fake"))
	return cmd
}

func (f *fakeMatchNotifyRedisClient) EvalRO(ctx context.Context, script string, keys []string, args ...interface{}) *redis.Cmd {
	return f.Eval(ctx, script, keys, args...)
}

func (f *fakeMatchNotifyRedisClient) EvalShaRO(ctx context.Context, sha1 string, keys []string, args ...interface{}) *redis.Cmd {
	return f.EvalSha(ctx, sha1, keys, args...)
}

func (f *fakeMatchNotifyRedisClient) ScriptExists(ctx context.Context, hashes ...string) *redis.BoolSliceCmd {
	return redis.NewBoolSliceResult(make([]bool, len(hashes)), nil)
}

func (f *fakeMatchNotifyRedisClient) ScriptLoad(ctx context.Context, script string) *redis.StringCmd {
	return redis.NewStringResult("fake-sha", nil)
}

type fakeRedisError string

func (e fakeRedisError) Error() string {
	return string(e)
}

func (e fakeRedisError) RedisError() {}

func TestMatchNotifyConsumer_Requeue_DoesNotIncreaseRetryCount(t *testing.T) {
	mr := mustRunMiniRedis(t)

	client := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer func() { _ = client.Close() }()

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
	if requeueErr := consumer.Requeue(ctx, msg); requeueErr != nil {
		t.Fatalf("Requeue: %v", requeueErr)
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
	defer func() { _ = client.Close() }()

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
	defer func() { _ = client.Close() }()

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
	defer func() { _ = client.Close() }()

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
	if nackErr := consumer.Nack(ctx, msg); nackErr != nil {
		t.Fatalf("Nack: %v", nackErr)
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
