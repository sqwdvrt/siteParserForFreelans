package api

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
)

func TestRequestIDMiddleware_GeneratesWhenMissing(t *testing.T) {
	r := chi.NewRouter()
	r.Use(RequestIDMiddleware())
	r.Get("/ping", func(w http.ResponseWriter, r *http.Request) {
		got := r.Header.Get(observability.HeaderRequestID)
		if got == "" {
			t.Fatal("request id missing in request header")
		}
		w.WriteHeader(http.StatusOK)
	})

	req := httptest.NewRequest(http.MethodGet, "/ping", nil)
	rr := httptest.NewRecorder()
	r.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d want=%d", rr.Code, http.StatusOK)
	}
	if got := rr.Header().Get(observability.HeaderRequestID); got == "" {
		t.Fatal("request id missing in response header")
	}
}

func TestRequestIDMiddleware_UsesIncomingValue(t *testing.T) {
	const incoming = "req-abc-123"
	r := chi.NewRouter()
	r.Use(RequestIDMiddleware())
	r.Get("/ping", func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get(observability.HeaderRequestID); got != incoming {
			t.Fatalf("request_id=%q want=%q", got, incoming)
		}
		w.WriteHeader(http.StatusOK)
	})

	req := httptest.NewRequest(http.MethodGet, "/ping", nil)
	req.Header.Set(observability.HeaderRequestID, "  "+incoming+" ")
	rr := httptest.NewRecorder()
	r.ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d want=%d", rr.Code, http.StatusOK)
	}
	if got := rr.Header().Get(observability.HeaderRequestID); got != incoming {
		t.Fatalf("response request_id=%q want=%q", got, incoming)
	}
}
