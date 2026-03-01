package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type stubNotifierPinger struct {
	err   error
	calls int
}

func (p *stubNotifierPinger) Ping(_ context.Context) error {
	p.calls++
	return p.err
}

func TestNotifierHealthz_AlwaysOK(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	rr := httptest.NewRecorder()

	notifierHealthz().ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	if strings.TrimSpace(rr.Body.String()) != "ok" {
		t.Fatalf("expected body ok, got %q", rr.Body.String())
	}
}

func TestNotifierReadyz_AllReady(t *testing.T) {
	db := &stubNotifierPinger{}
	redis := &stubNotifierPinger{}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	notifierReadyz(db, redis).ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	if strings.TrimSpace(rr.Body.String()) != "ok" {
		t.Fatalf("expected body ok, got %q", rr.Body.String())
	}
	if db.calls != 1 {
		t.Fatalf("expected 1 db ping, got %d", db.calls)
	}
	if redis.calls != 1 {
		t.Fatalf("expected 1 redis ping, got %d", redis.calls)
	}
}

func TestNotifierReadyz_DBNotReady(t *testing.T) {
	db := &stubNotifierPinger{err: errors.New("db down")}
	redis := &stubNotifierPinger{}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	notifierReadyz(db, redis).ServeHTTP(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "db not ready") {
		t.Fatalf("expected db error body, got %q", rr.Body.String())
	}
	if redis.calls != 0 {
		t.Fatalf("expected 0 redis pings after db failure, got %d", redis.calls)
	}
}

func TestNotifierReadyz_RedisNotReady(t *testing.T) {
	db := &stubNotifierPinger{}
	redis := &stubNotifierPinger{err: errors.New("redis down")}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	notifierReadyz(db, redis).ServeHTTP(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "redis not ready") {
		t.Fatalf("expected redis error body, got %q", rr.Body.String())
	}
	if db.calls != 1 {
		t.Fatalf("expected 1 db ping, got %d", db.calls)
	}
	if redis.calls != 1 {
		t.Fatalf("expected 1 redis ping, got %d", redis.calls)
	}
}
