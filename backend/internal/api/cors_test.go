package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func nopHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
}

func TestCORSMiddleware_AllowedOrigin(t *testing.T) {
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://example.com")
	mw := CORSMiddleware()
	handler := mw(nopHandler())

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("Origin", "https://example.com")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	if got := rr.Header().Get("Access-Control-Allow-Origin"); got != "https://example.com" {
		t.Errorf("expected ACAO header to be 'https://example.com', got %q", got)
	}
	if got := rr.Header().Get("Vary"); got != "Origin" {
		t.Errorf("expected Vary: Origin, got %q", got)
	}
}

func TestCORSMiddleware_UnknownOrigin(t *testing.T) {
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://example.com")
	mw := CORSMiddleware()
	handler := mw(nopHandler())

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("Origin", "https://evil.com")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	if got := rr.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Errorf("expected no ACAO header for unknown origin, got %q", got)
	}
	if got := rr.Header().Get("Vary"); got != "Origin" {
		t.Errorf("expected Vary: Origin even for unknown origin, got %q", got)
	}
}

func TestCORSMiddleware_Preflight(t *testing.T) {
	t.Setenv("CORS_ALLOWED_ORIGINS", "https://example.com")
	mw := CORSMiddleware()
	handler := mw(nopHandler())

	req := httptest.NewRequest(http.MethodOptions, "/", nil)
	req.Header.Set("Origin", "https://example.com")
	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("expected 204 for preflight, got %d", rr.Code)
	}
	if got := rr.Header().Get("Access-Control-Allow-Origin"); got != "https://example.com" {
		t.Errorf("expected ACAO header on preflight, got %q", got)
	}
	if got := rr.Header().Get("Access-Control-Allow-Methods"); got == "" {
		t.Error("expected Access-Control-Allow-Methods header on preflight")
	}
}
