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

type sequenceResponse struct {
	status int
	body   string
	err    error
}

type sequenceTransport struct {
	responses []sequenceResponse
	calls     int
}

func (t *sequenceTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if len(t.responses) == 0 {
		return nil, errors.New("no transport responses configured")
	}
	idx := t.calls
	if idx >= len(t.responses) {
		idx = len(t.responses) - 1
	}
	t.calls++
	resp := t.responses[idx]
	if resp.err != nil {
		return nil, resp.err
	}
	return &http.Response{
		StatusCode: resp.status,
		Body:       io.NopCloser(strings.NewReader(resp.body)),
		Header:     make(http.Header),
		Request:    req,
	}, nil
}

func TestFetcherRetriesTransientBrowserErrors(t *testing.T) {
	transport := &sequenceTransport{
		responses: []sequenceResponse{
			{status: http.StatusBadGateway, body: `{"detail":"upstream"}`},
			{status: http.StatusOK, body: `{"html":"<html>ok</html>","url":"https://kwork.ru/projects"}`},
		},
	}
	var sleeps []time.Duration
	client := &http.Client{Transport: transport, Timeout: time.Second}
	fetcher := newFetcher(
		"http://browser-service:8090",
		client,
		func(ctx context.Context, d time.Duration) error {
			sleeps = append(sleeps, d)
			return nil
		},
	)

	body, err := fetcher.Fetch(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	if string(body) != "<html>ok</html>" {
		t.Fatalf("unexpected body: %q", string(body))
	}
	if transport.calls != 2 {
		t.Fatalf("transport calls=%d want=2", transport.calls)
	}
	if len(sleeps) != 1 || sleeps[0] != defaultRetryBaseBackoff {
		t.Fatalf("unexpected backoff sequence: %v", sleeps)
	}
}

func TestFetcherPingChecksBrowserHealthEndpoint(t *testing.T) {
	transport := &sequenceTransport{
		responses: []sequenceResponse{
			{status: http.StatusOK, body: `ok`},
		},
	}
	client := &http.Client{Transport: transport, Timeout: time.Second}
	fetcher := newFetcher("http://browser-service:8090", client, nil)

	if err := fetcher.Ping(context.Background()); err != nil {
		t.Fatalf("Ping: %v", err)
	}
	if transport.calls != 1 {
		t.Fatalf("transport calls=%d want=1", transport.calls)
	}
}
