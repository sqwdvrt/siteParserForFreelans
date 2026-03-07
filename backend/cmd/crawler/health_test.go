package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type stubCrawlerPinger struct {
	err   error
	calls int
}

func (p *stubCrawlerPinger) Ping(_ context.Context) error {
	p.calls++
	return p.err
}

func TestCrawlerHealthz_AlwaysOK(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	rr := httptest.NewRecorder()

	crawlerHealthz().ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	if strings.TrimSpace(rr.Body.String()) != "ok" {
		t.Fatalf("expected body ok, got %q", rr.Body.String())
	}
}

func TestCrawlerReadyz_AllReady(t *testing.T) {
	db := &stubCrawlerPinger{}
	redis := &stubCrawlerPinger{}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	crawlerReadyz(db, redis, nil).ServeHTTP(rr, req)

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

func TestCrawlerReadyz_DBNotReady(t *testing.T) {
	db := &stubCrawlerPinger{err: errors.New("db down")}
	redis := &stubCrawlerPinger{}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	crawlerReadyz(db, redis, nil).ServeHTTP(rr, req)

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

func TestCrawlerReadyz_RedisNotReady(t *testing.T) {
	db := &stubCrawlerPinger{}
	redis := &stubCrawlerPinger{err: errors.New("redis down")}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	crawlerReadyz(db, redis, nil).ServeHTTP(rr, req)

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

func TestCrawlerReadyz_BrowserServiceNotReady(t *testing.T) {
	db := &stubCrawlerPinger{}
	redis := &stubCrawlerPinger{}
	browser := &stubCrawlerPinger{err: errors.New("browser down")}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	crawlerReadyz(db, redis, browser).ServeHTTP(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "browser service not ready") {
		t.Fatalf("expected browser error body, got %q", rr.Body.String())
	}
	if browser.calls != 1 {
		t.Fatalf("expected 1 browser ping, got %d", browser.calls)
	}
}

func TestParsePositiveIntEnv_ValidAndInvalid(t *testing.T) {
	t.Setenv("CRAWL_BREAKER_FAILURE_THRESHOLD", "5")
	v, err := parsePositiveIntEnv("CRAWL_BREAKER_FAILURE_THRESHOLD", 3)
	if err != nil {
		t.Fatalf("unexpected err for valid int: %v", err)
	}
	if v != 5 {
		t.Fatalf("want 5, got %d", v)
	}

	t.Setenv("CRAWL_BREAKER_FAILURE_THRESHOLD", "0")
	if _, err := parsePositiveIntEnv("CRAWL_BREAKER_FAILURE_THRESHOLD", 3); err == nil {
		t.Fatal("expected error for non-positive int")
	}
}

func TestParsePositiveDurationEnv_ValidAndInvalid(t *testing.T) {
	t.Setenv("CRAWL_BREAKER_OPEN_INTERVAL", "45s")
	v, err := parsePositiveDurationEnv("CRAWL_BREAKER_OPEN_INTERVAL", time.Minute)
	if err != nil {
		t.Fatalf("unexpected err for valid duration: %v", err)
	}
	if v != 45*time.Second {
		t.Fatalf("want 45s, got %v", v)
	}

	t.Setenv("CRAWL_BREAKER_OPEN_INTERVAL", "0s")
	if _, err := parsePositiveDurationEnv("CRAWL_BREAKER_OPEN_INTERVAL", time.Minute); err == nil {
		t.Fatal("expected error for non-positive duration")
	}
}
