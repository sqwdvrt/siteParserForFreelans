package telegram

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockTransport struct {
	status int
}

func (m *mockTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return &http.Response{
		StatusCode: m.status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}, nil
}

type captureTransport struct {
	status   int
	lastBody string
}

func (m *captureTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req != nil && req.Body != nil {
		raw, _ := io.ReadAll(req.Body)
		m.lastBody = string(raw)
	}
	return &http.Response{
		StatusCode: m.status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}, nil
}

func TestNotifier_Send_Success(t *testing.T) {
	n := NewNotifierWithClient("test-token", &http.Client{
		Transport: &mockTransport{status: 200},
		Timeout:   5 * time.Second,
	})
	ctx := context.Background()
	job := &domain.Job{
		ID:          1,
		Title:       "Test Job",
		Description: "Desc",
		URL:         "https://kwork.ru/projects/1",
	}
	err := n.Send(ctx, 123456, port.NotifyPayload{Job: job, Score: 0.85})
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
}

func TestNotifier_Send_429_Retry(t *testing.T) {
	attempts := 0
	transport := &mockTransportWithCount{statusFirst: 429, statusLater: 200, attempts: &attempts}
	n := NewNotifierWithClient("token", &http.Client{
		Transport: transport,
		Timeout:   5 * time.Second,
	})
	ctx := context.Background()
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err != nil {
		t.Fatalf("Send after retry: %v", err)
	}
	if attempts < 2 {
		t.Errorf("want at least 2 attempts (retry), got %d", attempts)
	}
}

func TestNotifier_Send_5xx_RetryThenFail(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransport{status: 500},
		Timeout:   5 * time.Second,
	})
	ctx := context.Background()
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want error on 500 after retries")
	}
	if err.Error() != "telegram api: http 500" {
		t.Errorf("want 'telegram api: http 500', got %v", err)
	}
}

func TestNotifier_Send_NilJob(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{Transport: &mockTransport{status: 200}})
	err := n.Send(context.Background(), 123, port.NotifyPayload{Job: nil, Score: 0.5})
	if err == nil {
		t.Fatal("want error on nil job")
	}
	if err.Error() != "job is nil" {
		t.Errorf("want 'job is nil', got %v", err)
	}
}

func TestNotifier_Send_BatchPayload_Success(t *testing.T) {
	transport := &captureTransport{status: 200}
	n := NewNotifierWithClient("token", &http.Client{
		Transport: transport,
		Timeout:   5 * time.Second,
	})
	err := n.Send(context.Background(), 123456, port.NotifyPayload{
		Batch: []port.BatchNotifyItem{
			{
				Job: &domain.Job{
					ID:    2,
					Title: "Second",
					URL:   "https://kwork.ru/projects/2",
				},
				WhyItFits: "Second reason",
				Rank:      2,
			},
			{
				Job: &domain.Job{
					ID:    1,
					Title: "First",
					URL:   "https://kwork.ru/projects/1",
				},
				WhyItFits: "First reason",
				Rank:      1,
			},
		},
		CriticScore: 8.2,
	})
	if err != nil {
		t.Fatalf("Send batch: %v", err)
	}

	var body map[string]any
	if err := json.Unmarshal([]byte(transport.lastBody), &body); err != nil {
		t.Fatalf("decode request body: %v", err)
	}
	text, _ := body["text"].(string)
	if !strings.Contains(text, "Подборка для вас") {
		t.Fatalf("batch text header missing: %q", text)
	}
	if !strings.Contains(text, "8.2/10") {
		t.Fatalf("batch score missing: %q", text)
	}
	if strings.Index(text, "<b>1. First</b>") == -1 || strings.Index(text, "<b>2. Second</b>") == -1 {
		t.Fatalf("ranked titles missing: %q", text)
	}
	if !strings.Contains(text, `Открыть #1`) || !strings.Contains(text, `Открыть #2`) {
		t.Fatalf("batch links labels missing: %q", text)
	}
}

func TestFormatBatchMessage_SortsByRank(t *testing.T) {
	msg := formatBatchMessage(port.NotifyPayload{
		Batch: []port.BatchNotifyItem{
			{
				Job: &domain.Job{
					Title: "Later",
					URL:   "https://kwork.ru/projects/2",
				},
				Rank: 2,
			},
			{
				Job: &domain.Job{
					Title: "Earlier",
					URL:   "https://kwork.ru/projects/1",
				},
				Rank: 1,
			},
		},
		CriticScore: 7.7,
	})
	if !strings.Contains(msg, "🎯 <b>Подборка для вас</b> (оценка: 7.7/10)") {
		t.Fatalf("batch header not found: %q", msg)
	}
	firstIdx := strings.Index(msg, "<b>1. Earlier</b>")
	secondIdx := strings.Index(msg, "<b>2. Later</b>")
	if firstIdx == -1 || secondIdx == -1 {
		t.Fatalf("sorted items not found: %q", msg)
	}
	if firstIdx > secondIdx {
		t.Fatalf("items order invalid: %q", msg)
	}
}

func TestNotifier_Send_ErrorDoesNotContainToken(t *testing.T) {
	n := NewNotifierWithClient("secret-bot-token-12345", &http.Client{
		Transport: &mockTransport{status: 401},
		Timeout:   5 * time.Second,
	})
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(context.Background(), 1, port.NotifyPayload{Job: job, Score: 0.5})
	if err == nil {
		t.Fatal("want error")
	}
	errStr := err.Error()
	if strings.Contains(errStr, "secret") || strings.Contains(errStr, "token") || strings.Contains(errStr, "12345") {
		t.Errorf("error must not contain token, got: %s", errStr)
	}
}

func TestFormatMessage_TruncateDescription_UTF8Safe(t *testing.T) {
	desc := strings.Repeat("🙂", maxDescLen+1)
	job := &domain.Job{
		ID:          1,
		Title:       "Emoji job",
		Description: desc,
		URL:         "https://kwork.ru/projects/1",
	}

	msg := formatMessage(port.NotifyPayload{Job: job, Score: 0.85})
	wantDesc := strings.Repeat("🙂", maxDescLen) + "..."

	if !utf8.ValidString(msg) {
		t.Fatal("message must be valid UTF-8")
	}
	if strings.Contains(msg, "�") {
		t.Fatalf("message contains broken UTF-8 replacement rune: %q", msg)
	}
	if !strings.Contains(msg, wantDesc) {
		t.Fatalf("want truncated description by runes, got: %q", msg)
	}
}

type mockTransportWithCount struct {
	statusFirst int
	statusLater int
	attempts    *int
}

func (m *mockTransportWithCount) RoundTrip(req *http.Request) (*http.Response, error) {
	*m.attempts++
	status := m.statusFirst
	if *m.attempts > 1 {
		status = m.statusLater
	}
	return &http.Response{
		StatusCode: status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}, nil
}

type mockTransportWithBodyAndCount struct {
	status   int
	body     string
	attempts *int
}

func (m *mockTransportWithBodyAndCount) RoundTrip(req *http.Request) (*http.Response, error) {
	*m.attempts++
	return &http.Response{
		StatusCode: m.status,
		Body:       io.NopCloser(strings.NewReader(m.body)),
		Header:     make(http.Header),
	}, nil
}

type errorTransport struct {
	err error
}

func (m *errorTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if m.err == nil {
		m.err = errors.New("network down")
	}
	return nil, m.err
}

func TestNotifier_Send_RespectsContextCancel_DuringRetryOnHTTPStatus(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransport{status: 500},
		Timeout:   5 * time.Second,
	})
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(50*time.Millisecond, cancel)

	start := time.Now()
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	elapsed := time.Since(start)

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context canceled, got %v", err)
	}
	if elapsed >= retryBaseWait {
		t.Fatalf("Send waited too long after cancel: %v", elapsed)
	}
}

func TestNotifier_Send_RespectsContextCancel_DuringRetryOnRequestError(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &errorTransport{},
		Timeout:   5 * time.Second,
	})
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(50*time.Millisecond, cancel)

	start := time.Now()
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	elapsed := time.Since(start)

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context canceled, got %v", err)
	}
	if elapsed >= retryBaseWait {
		t.Fatalf("Send waited too long after cancel: %v", elapsed)
	}
}

func TestNotifier_Send_429_OpensCircuitWithRetryAfter(t *testing.T) {
	attempts := 0
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransportWithBodyAndCount{
			status:   429,
			body:     `{"ok":false,"parameters":{"retry_after":2}}`,
			attempts: &attempts,
		},
		Timeout: 5 * time.Second,
	})
	n.maxRetries = 1
	n.retryBaseWait = 10 * time.Millisecond
	n.breaker = newCircuitBreaker(3, 100*time.Millisecond, 0)

	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want error on 429")
	}
	delay, ok := RetryAfter(err)
	if !ok {
		t.Fatalf("want retryable error on 429, got %v", err)
	}
	if delay != 2*time.Second {
		t.Fatalf("retry delay = %v, want 2s", delay)
	}

	err = n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want circuit-open error on immediate second send")
	}
	if !strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("want circuit-open error, got %v", err)
	}
	if attempts != 1 {
		t.Fatalf("circuit-open call must not hit transport, attempts=%d", attempts)
	}
	if !ShouldRequeueWithoutRetry(err) {
		t.Fatal("circuit-open error must be requeued without retry increment")
	}
}

func TestNotifier_Send_5xx_OpensCircuitAfterThreshold(t *testing.T) {
	attempts := 0
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransportWithBodyAndCount{
			status:   500,
			body:     "",
			attempts: &attempts,
		},
		Timeout: 5 * time.Second,
	})
	n.maxRetries = 1
	n.retryBaseWait = 10 * time.Millisecond
	n.breaker = newCircuitBreaker(2, 150*time.Millisecond, 0)

	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want first 5xx error")
	}
	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want second 5xx error")
	}
	err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want circuit-open error")
	}
	if !strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("want circuit-open error, got %v", err)
	}
	if attempts != 2 {
		t.Fatalf("circuit-open call must not hit transport, attempts=%d", attempts)
	}
}

func TestNotifier_Send_5xx_ThresholdCountsPerSendNotPerInternalRetry(t *testing.T) {
	attempts := 0
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransportWithBodyAndCount{
			status:   500,
			body:     "",
			attempts: &attempts,
		},
		Timeout: 5 * time.Second,
	})
	n.maxRetries = 3
	n.retryBaseWait = 1 * time.Millisecond
	n.breaker = newCircuitBreaker(3, 100*time.Millisecond, 0)

	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want first 5xx error")
	}
	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want second 5xx error")
	}
	err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want third 5xx error")
	}
	if strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("third send must still perform request retries, got %v", err)
	}
	if attempts != 9 {
		t.Fatalf("want 9 real HTTP attempts before opening breaker, got %d", attempts)
	}

	err = n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want circuit-open error after threshold reached")
	}
	if !strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("want circuit-open error, got %v", err)
	}
	if attempts != 9 {
		t.Fatalf("circuit-open call must not hit transport, attempts=%d", attempts)
	}
}

func TestRetryAfter_DetectsWrappedRetryableError(t *testing.T) {
	err := fmt.Errorf("wrapped: %w", newRetryableError("retryable", 3*time.Second))
	delay, ok := RetryAfter(err)
	if !ok {
		t.Fatal("want wrapped retryable error to be detected")
	}
	if delay != 3*time.Second {
		t.Fatalf("retry delay = %v, want 3s", delay)
	}
}

func TestShouldRequeueWithoutRetry_OnlyForCircuitOpen(t *testing.T) {
	if !ShouldRequeueWithoutRetry(newCircuitOpenError(2 * time.Second)) {
		t.Fatal("circuit-open should request requeue without retry increment")
	}
	if ShouldRequeueWithoutRetry(newRetryableError("telegram api: http 500", time.Second)) {
		t.Fatal("regular retryable errors must consume retry budget")
	}
}

func TestNotifier_Configure_AppliesRetryBreakerConfig(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{Transport: &mockTransport{status: 200}})

	n.Configure(Config{
		MaxRetries:              7,
		RetryBaseWait:           250 * time.Millisecond,
		BreakerFailureThreshold: 9,
		BreakerOpenInterval:     45 * time.Second,
		BreakerOpenJitter:       0.35,
	})

	if n.maxRetries != 7 {
		t.Fatalf("maxRetries = %d, want 7", n.maxRetries)
	}
	if n.retryBaseWait != 250*time.Millisecond {
		t.Fatalf("retryBaseWait = %v, want 250ms", n.retryBaseWait)
	}
	if n.breakerFailureThreshold != 9 {
		t.Fatalf("breakerFailureThreshold = %d, want 9", n.breakerFailureThreshold)
	}
	if n.breakerOpenInterval != 45*time.Second {
		t.Fatalf("breakerOpenInterval = %v, want 45s", n.breakerOpenInterval)
	}
	if n.breakerOpenJitter != 0.35 {
		t.Fatalf("breakerOpenJitter = %v, want 0.35", n.breakerOpenJitter)
	}
	if n.breaker == nil {
		t.Fatal("breaker must be rebuilt after Configure")
	}
	if n.breaker.failureThreshold != 9 {
		t.Fatalf("breaker.failureThreshold = %d, want 9", n.breaker.failureThreshold)
	}
	if n.breaker.openInterval != 45*time.Second {
		t.Fatalf("breaker.openInterval = %v, want 45s", n.breaker.openInterval)
	}
	if n.breaker.openJitter != 0.35 {
		t.Fatalf("breaker.openJitter = %v, want 0.35", n.breaker.openJitter)
	}
}

func TestCircuitBreaker_Open_UsesPositiveJitter(t *testing.T) {
	now := time.Unix(100, 0)
	b := newCircuitBreaker(1, 10*time.Second, 0.5)
	b.randFloat64 = func() float64 { return 1 } // max jitter

	b.markTransientFailure(now, 10*time.Second, false)

	if b.state != breakerOpen {
		t.Fatalf("state = %v, want open", b.state)
	}
	got := b.openedUntil.Sub(now)
	if got != 15*time.Second {
		t.Fatalf("open window = %v, want 15s", got)
	}
}

func TestCircuitBreaker_HalfOpenInFlight_UsesPositiveJitterOnWait(t *testing.T) {
	now := time.Unix(200, 0)
	b := newCircuitBreaker(1, 10*time.Second, 0.5)
	b.randFloat64 = func() float64 { return 1 } // max jitter
	b.state = breakerHalfOpen
	b.halfOpenInFlight = true
	b.openedUntil = now.Add(10 * time.Second)

	wait, allowed := b.beforeRequest(now)
	if allowed {
		t.Fatal("half-open in-flight must reject concurrent probe")
	}
	if wait != 15*time.Second {
		t.Fatalf("wait = %v, want 15s", wait)
	}
}
