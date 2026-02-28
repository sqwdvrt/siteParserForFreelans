package api

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
)

func TestRequestLoggingMiddleware_LogsStructuredFields(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&buf, nil))

	r := chi.NewRouter()
	r.Use(RequestLoggingMiddleware(logger))
	r.Get("/users/{id}", func(w http.ResponseWriter, req *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})

	req := httptest.NewRequest(http.MethodGet, "/users/42", nil)
	req.RemoteAddr = "203.0.113.10:12345"
	req.Header.Set("User-Agent", "test-agent/1.0")
	req.Header.Set("X-Request-ID", "req-123")
	rr := httptest.NewRecorder()
	r.ServeHTTP(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", rr.Code, http.StatusNoContent)
	}

	var entry map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &entry); err != nil {
		t.Fatalf("decode log entry: %v", err)
	}

	if got := entry["msg"]; got != "http request" {
		t.Fatalf("msg = %v, want %q", got, "http request")
	}
	if got := entry["method"]; got != "GET" {
		t.Fatalf("method = %v, want %q", got, "GET")
	}
	if got := entry["route"]; got != "/users/{id}" {
		t.Fatalf("route = %v, want %q", got, "/users/{id}")
	}
	if got := entry["status"]; got != float64(http.StatusNoContent) {
		t.Fatalf("status = %v, want %d", got, http.StatusNoContent)
	}
	if got := entry["client_ip"]; got != "203.0.113.10" {
		t.Fatalf("client_ip = %v, want %q", got, "203.0.113.10")
	}
	if got := entry["request_id"]; got != "req-123" {
		t.Fatalf("request_id = %v, want %q", got, "req-123")
	}
	if got := entry["user_agent"]; got != "test-agent/1.0" {
		t.Fatalf("user_agent = %v, want %q", got, "test-agent/1.0")
	}
	if _, ok := entry["duration"]; !ok {
		t.Fatal("duration is missing in log entry")
	}
}

func TestRequestLoggingMiddleware_UsesErrorLevelFor5xx(t *testing.T) {
	var buf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&buf, nil))

	r := chi.NewRouter()
	r.Use(RequestLoggingMiddleware(logger))
	r.Get("/boom", func(w http.ResponseWriter, req *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	})

	req := httptest.NewRequest(http.MethodGet, "/boom", nil)
	rr := httptest.NewRecorder()
	r.ServeHTTP(rr, req)

	var entry map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &entry); err != nil {
		t.Fatalf("decode log entry: %v", err)
	}
	if got := entry["level"]; got != "ERROR" {
		t.Fatalf("level = %v, want %q", got, "ERROR")
	}
	if got := entry["status"]; got != float64(http.StatusInternalServerError) {
		t.Fatalf("status = %v, want %d", got, http.StatusInternalServerError)
	}
}
