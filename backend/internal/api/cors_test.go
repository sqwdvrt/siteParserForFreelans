package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func newCORSMiddlewareWithOrigins(origins string) func(http.Handler) http.Handler {
	t := testing.T{}
	_ = t
	// Use a stub that mimics CORSMiddleware but with explicit origins map
	// to avoid env var pollution in tests.
	allowed := map[string]struct{}{}
	for _, o := range splitTrimmed(origins) {
		allowed[o] = struct{}{}
	}
	return buildCORSHandler(allowed)
}

func splitTrimmed(s string) []string {
	parts := []string{}
	for _, p := range splitComma(s) {
		if p != "" {
			parts = append(parts, p)
		}
	}
	return parts
}

func splitComma(s string) []string {
	result := []string{}
	cur := ""
	for _, c := range s {
		if c == ',' {
			result = append(result, trimSpace(cur))
			cur = ""
		} else {
			cur += string(c)
		}
	}
	result = append(result, trimSpace(cur))
	return result
}

func trimSpace(s string) string {
	start, end := 0, len(s)
	for start < end && (s[start] == ' ' || s[start] == '\t') {
		start++
	}
	for end > start && (s[end-1] == ' ' || s[end-1] == '\t') {
		end--
	}
	return s[start:end]
}

func buildCORSHandler(allowed map[string]struct{}) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			if _, ok := allowed[origin]; ok {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Request-ID")
				w.Header().Set("Vary", "Origin")
			}
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

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
