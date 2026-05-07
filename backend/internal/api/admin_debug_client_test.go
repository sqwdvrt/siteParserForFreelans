package api

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func TestHTTPAdminDebugMatchClient_GetMatchDebug(t *testing.T) {
	var gotPath string
	client := &HTTPAdminDebugMatchClient{
		BaseURL: "https://debug.internal",
		Client: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
			gotPath = r.URL.String()
			return &http.Response{
				StatusCode: http.StatusOK,
				Body:       io.NopCloser(strings.NewReader(`{"matched":true}`)),
				Header:     http.Header{"Content-Type": []string{"application/json"}},
			}, nil
		})},
	}
	payload, err := client.GetMatchDebug(context.Background(), 7, "https://example.com/jobs/1?a=1")
	if err != nil {
		t.Fatalf("GetMatchDebug err=%v", err)
	}
	if gotPath != "https://debug.internal/internal/debug/match?user_id=7&job_url=https%3A%2F%2Fexample.com%2Fjobs%2F1%3Fa%3D1" {
		t.Fatalf("path=%q", gotPath)
	}
	if matched, ok := payload["matched"].(bool); !ok || !matched {
		t.Fatalf("payload=%#v want matched=true", payload)
	}
}

func TestHTTPAdminDebugMatchClient_GetMatchDebugUpstreamError(t *testing.T) {
	client := &HTTPAdminDebugMatchClient{
		BaseURL: "https://debug.internal",
		Client: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
			return &http.Response{
				StatusCode: http.StatusBadGateway,
				Body:       io.NopCloser(strings.NewReader("nope")),
			}, nil
		})},
	}
	if _, err := client.GetMatchDebug(context.Background(), 7, "https://example.com/jobs/1"); err == nil {
		t.Fatal("expected upstream status error")
	}
}
