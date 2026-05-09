package browser

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type errReadCloser struct{}

func (errReadCloser) Read([]byte) (int, error) { return 0, errors.New("read failed") }
func (errReadCloser) Close() error             { return nil }

type customTransport func(*http.Request) (*http.Response, error)

func (t customTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return t(req)
}

func TestNewFetcherDefaultsAndTrimsTrailingSlash(t *testing.T) {
	fetcher := NewFetcher("http://browser-service:8090/")
	if fetcher.serviceURL != "http://browser-service:8090" {
		t.Fatalf("serviceURL=%q", fetcher.serviceURL)
	}
	if fetcher.client == nil || fetcher.client.Timeout != defaultTimeout {
		t.Fatalf("client timeout=%v", fetcher.client.Timeout)
	}
}

func TestFetcherFetchReturnsTruncatedHTTPError(t *testing.T) {
	client := &http.Client{Transport: customTransport(func(req *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusBadRequest,
			Body:       io.NopCloser(strings.NewReader(strings.Repeat("x", 250))),
			Header:     make(http.Header),
			Request:    req,
		}, nil
	})}
	fetcher := newFetcher("http://browser-service:8090", client, nil)

	_, err := fetcher.Fetch(context.Background(), "https://example.com")
	if err == nil || !strings.Contains(err.Error(), "http 400") || !strings.Contains(err.Error(), "...") {
		t.Fatalf("err=%v", err)
	}
}

func TestFetcherFetchReturnsDecodeAndEmptyHTMLErrors(t *testing.T) {
	tests := []struct {
		name string
		body string
		want string
	}{
		{name: "decode", body: `{"html":`, want: "decode response"},
		{name: "empty-html", body: `{"html":"","url":"https://example.com"}`, want: "empty html"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			client := &http.Client{Transport: customTransport(func(req *http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: http.StatusOK,
					Body:       io.NopCloser(strings.NewReader(tt.body)),
					Header:     make(http.Header),
					Request:    req,
				}, nil
			})}
			fetcher := newFetcher("http://browser-service:8090", client, nil)

			_, err := fetcher.Fetch(context.Background(), "https://example.com")
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("err=%v want contains %q", err, tt.want)
			}
		})
	}
}

func TestFetcherFetchReturnsReadBodyAndRequestErrors(t *testing.T) {
	readClient := &http.Client{Transport: customTransport(func(req *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusOK,
			Body:       errReadCloser{},
			Header:     make(http.Header),
			Request:    req,
		}, nil
	})}
	fetcher := newFetcher("http://browser-service:8090", readClient, nil)
	if _, err := fetcher.Fetch(context.Background(), "https://example.com"); err == nil || !strings.Contains(err.Error(), "read body") {
		t.Fatalf("read body err=%v", err)
	}

	reqClient := &http.Client{Transport: customTransport(func(req *http.Request) (*http.Response, error) {
		return nil, errors.New("dial tcp timeout")
	})}
	retryingFetcher := newFetcher("http://browser-service:8090", reqClient, func(context.Context, time.Duration) error { return nil })
	retryingFetcher.retryMaxAttempts = 2
	if _, err := retryingFetcher.Fetch(context.Background(), "https://example.com"); err == nil || !strings.Contains(err.Error(), "request") {
		t.Fatalf("request err=%v", err)
	}
}

func TestFetcherFetchStopsOnSleepContextError(t *testing.T) {
	transport := &sequenceTransport{
		responses: []sequenceResponse{
			{status: http.StatusTooManyRequests, body: `{"detail":"slow down"}`},
		},
	}
	fetcher := newFetcher("http://browser-service:8090", &http.Client{Transport: transport}, func(context.Context, time.Duration) error {
		return context.DeadlineExceeded
	})
	fetcher.retryMaxAttempts = 3

	_, err := fetcher.Fetch(context.Background(), "https://example.com")
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err=%v", err)
	}
}

func TestFetcherPingFailures(t *testing.T) {
	badStatusClient := &http.Client{Transport: customTransport(func(req *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusBadGateway,
			Body:       io.NopCloser(strings.NewReader("bad gateway")),
			Header:     make(http.Header),
			Request:    req,
		}, nil
	})}
	if err := newFetcher("http://browser-service:8090", badStatusClient, nil).Ping(context.Background()); err == nil || !strings.Contains(err.Error(), "http 502") {
		t.Fatalf("Ping bad status err=%v", err)
	}

	reqErrClient := &http.Client{Transport: customTransport(func(req *http.Request) (*http.Response, error) {
		return nil, errors.New("connection refused")
	})}
	if err := newFetcher("http://browser-service:8090", reqErrClient, nil).Ping(context.Background()); err == nil || !strings.Contains(err.Error(), "request") {
		t.Fatalf("Ping request err=%v", err)
	}
}

func TestRetryHelpers(t *testing.T) {
	fetcher := newFetcher("http://browser-service:8090", nil, nil)
	if got := fetcher.retryBackoff(0); got != defaultRetryBaseBackoff {
		t.Fatalf("retryBackoff(0)=%v", got)
	}
	if got := fetcher.retryBackoff(4); got > defaultRetryMaxBackoff {
		t.Fatalf("retryBackoff(4)=%v", got)
	}
	if !shouldRetryStatus(http.StatusTooManyRequests) || !shouldRetryStatus(http.StatusInternalServerError) || shouldRetryStatus(http.StatusBadRequest) {
		t.Fatal("shouldRetryStatus mismatch")
	}
	if got := truncate("short", 10); got != "short" {
		t.Fatalf("truncate short=%q", got)
	}
	if got := truncate(strings.Repeat("a", 5), 3); got != "aaa..." {
		t.Fatalf("truncate long=%q", got)
	}
}

func TestSleepWithContext(t *testing.T) {
	if err := sleepWithContext(context.Background(), 0); err != nil {
		t.Fatalf("sleepWithContext zero: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := sleepWithContext(ctx, time.Second); !errors.Is(err, context.Canceled) {
		t.Fatalf("sleepWithContext canceled=%v", err)
	}
}
