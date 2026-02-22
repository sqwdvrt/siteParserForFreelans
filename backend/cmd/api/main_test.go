package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type stubPinger struct {
	err   error
	calls int
}

func (p *stubPinger) Ping(context.Context) error {
	p.calls++
	return p.err
}

func TestParseTrustedProxyCIDRs_Empty(t *testing.T) {
	nets, err := parseTrustedProxyCIDRs("")
	if err != nil {
		t.Fatalf("parse empty: %v", err)
	}
	if len(nets) != 0 {
		t.Fatalf("expected 0 cidrs, got %d", len(nets))
	}
}

func TestParseTrustedProxyCIDRs_ValidList(t *testing.T) {
	nets, err := parseTrustedProxyCIDRs("127.0.0.1/32, 10.0.0.0/8")
	if err != nil {
		t.Fatalf("parse valid list: %v", err)
	}
	if len(nets) != 2 {
		t.Fatalf("expected 2 cidrs, got %d", len(nets))
	}
}

func TestParseTrustedProxyCIDRs_Invalid(t *testing.T) {
	_, err := parseTrustedProxyCIDRs("not-a-cidr")
	if err == nil {
		t.Fatal("expected parse error for invalid cidr")
	}
}

func TestParseOptionalBoolEnv_DefaultFalse(t *testing.T) {
	v, err := parseOptionalBoolEnv("API_ALLOW_REDIS_DEGRADED", "")
	if err != nil {
		t.Fatalf("parse bool: %v", err)
	}
	if v {
		t.Fatal("expected false for empty value")
	}
}

func TestParseOptionalBoolEnv_TrueValues(t *testing.T) {
	trueValues := []string{"1", "true", "TRUE", "yes", "on"}
	for _, raw := range trueValues {
		v, err := parseOptionalBoolEnv("API_ALLOW_REDIS_DEGRADED", raw)
		if err != nil {
			t.Fatalf("parse %q: %v", raw, err)
		}
		if !v {
			t.Fatalf("expected true for %q", raw)
		}
	}
}

func TestParseOptionalBoolEnv_FalseValues(t *testing.T) {
	falseValues := []string{"0", "false", "FALSE", "no", "off"}
	for _, raw := range falseValues {
		v, err := parseOptionalBoolEnv("API_ALLOW_REDIS_DEGRADED", raw)
		if err != nil {
			t.Fatalf("parse %q: %v", raw, err)
		}
		if v {
			t.Fatalf("expected false for %q", raw)
		}
	}
}

func TestParseOptionalBoolEnv_Invalid(t *testing.T) {
	_, err := parseOptionalBoolEnv("API_ALLOW_REDIS_DEGRADED", "maybe")
	if err == nil {
		t.Fatal("expected parse error for invalid bool")
	}
}

func TestValidateRuntimeSecurityPolicy_ProductionRejectsDegraded(t *testing.T) {
	err := validateRuntimeSecurityPolicy(true, true)
	if err == nil {
		t.Fatal("expected error when production allows degraded mode")
	}
}

func TestValidateRuntimeSecurityPolicy_AllowsSafeCombinations(t *testing.T) {
	cases := []struct {
		name               string
		isProd             bool
		allowRedisDegraded bool
	}{
		{name: "dev_degraded", isProd: false, allowRedisDegraded: true},
		{name: "dev_normal", isProd: false, allowRedisDegraded: false},
		{name: "prod_normal", isProd: true, allowRedisDegraded: false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := validateRuntimeSecurityPolicy(tc.isProd, tc.allowRedisDegraded); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
}

func TestHealthz_RedisReady(t *testing.T) {
	redis := &stubPinger{}
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	rr := httptest.NewRecorder()

	healthz(redis).ServeHTTP(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	if strings.TrimSpace(rr.Body.String()) != "ok" {
		t.Fatalf("expected body ok, got %q", rr.Body.String())
	}
	if redis.calls != 1 {
		t.Fatalf("expected 1 redis ping, got %d", redis.calls)
	}
}

func TestHealthz_RedisNotReady(t *testing.T) {
	redis := &stubPinger{err: errors.New("redis down")}
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	rr := httptest.NewRecorder()

	healthz(redis).ServeHTTP(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "redis not ready") {
		t.Fatalf("expected redis error body, got %q", rr.Body.String())
	}
	if redis.calls != 1 {
		t.Fatalf("expected 1 redis ping, got %d", redis.calls)
	}
}

func TestReadyz_RedisReady(t *testing.T) {
	db := &stubPinger{}
	redis := &stubPinger{}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	readyz(db, redis).ServeHTTP(rr, req)

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

func TestReadyz_RedisNotReady(t *testing.T) {
	db := &stubPinger{}
	redis := &stubPinger{err: errors.New("redis down")}
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	rr := httptest.NewRecorder()

	readyz(db, redis).ServeHTTP(rr, req)

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
