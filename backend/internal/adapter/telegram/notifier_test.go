package telegram

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockTransport struct {
	status int
}

func (m *mockTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return &http.Response{
		StatusCode: m.status,
		Body:       io.NopCloser(nil),
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
		Body:       io.NopCloser(nil),
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
