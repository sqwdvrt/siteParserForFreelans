package main

import (
	"context"
	"os"
	"testing"
	"time"
)

func TestNextPopErrorBackoff_GrowthAndCap(t *testing.T) {
	if got := nextPopErrorBackoff(0); got != popErrorBackoffMin {
		t.Fatalf("first backoff = %v, want %v", got, popErrorBackoffMin)
	}

	b := popErrorBackoffMin
	for b < popErrorBackoffMax {
		next := nextPopErrorBackoff(b)
		if next < b {
			t.Fatalf("backoff decreased: prev=%v next=%v", b, next)
		}
		if next > popErrorBackoffMax {
			t.Fatalf("backoff exceeds max: %v > %v", next, popErrorBackoffMax)
		}
		b = next
		if b == popErrorBackoffMax {
			break
		}
	}

	if got := nextPopErrorBackoff(popErrorBackoffMax); got != popErrorBackoffMax {
		t.Fatalf("capped backoff = %v, want %v", got, popErrorBackoffMax)
	}
}

func TestWaitForBackoff_ContextCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	start := time.Now()
	ok := waitForBackoff(ctx, 10*time.Second)
	elapsed := time.Since(start)
	if ok {
		t.Fatal("waitForBackoff returned true for canceled context")
	}
	if elapsed > 100*time.Millisecond {
		t.Fatalf("waitForBackoff took too long after cancel: %v", elapsed)
	}
}

func TestNextNackRecoverBackoff_GrowthAndCap(t *testing.T) {
	if got := nextNackRecoverBackoff(0); got != nackRecoverBackoffMin {
		t.Fatalf("first backoff = %v, want %v", got, nackRecoverBackoffMin)
	}

	b := nackRecoverBackoffMin
	for b < nackRecoverBackoffMax {
		next := nextNackRecoverBackoff(b)
		if next < b {
			t.Fatalf("backoff decreased: prev=%v next=%v", b, next)
		}
		if next > nackRecoverBackoffMax {
			t.Fatalf("backoff exceeds max: %v > %v", next, nackRecoverBackoffMax)
		}
		b = next
		if b == nackRecoverBackoffMax {
			break
		}
	}

	if got := nextNackRecoverBackoff(nackRecoverBackoffMax); got != nackRecoverBackoffMax {
		t.Fatalf("capped backoff = %v, want %v", got, nackRecoverBackoffMax)
	}
}

func TestNotifierEnvParsing_FallbacksForInvalidValues(t *testing.T) {
	t.Setenv("NOTIFIER_MAX_RETRIES", "bad")
	t.Setenv("NOTIFIER_RETRY_BASE_WAIT", "not-duration")
	t.Setenv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", "-1")
	t.Setenv("NOTIFIER_BREAKER_OPEN_INTERVAL", "0s")
	t.Setenv("NOTIFIER_BREAKER_OPEN_JITTER", "1.5")

	if got := getPositiveIntEnv("NOTIFIER_MAX_RETRIES", 3); got != 3 {
		t.Fatalf("NOTIFIER_MAX_RETRIES fallback = %d, want 3", got)
	}
	if got := getDurationEnv("NOTIFIER_RETRY_BASE_WAIT", time.Second); got != time.Second {
		t.Fatalf("NOTIFIER_RETRY_BASE_WAIT fallback = %v, want 1s", got)
	}
	if got := getPositiveIntEnv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", 3); got != 3 {
		t.Fatalf("NOTIFIER_BREAKER_FAILURE_THRESHOLD fallback = %d, want 3", got)
	}
	if got := getDurationEnv("NOTIFIER_BREAKER_OPEN_INTERVAL", 30*time.Second); got != 30*time.Second {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_INTERVAL fallback = %v, want 30s", got)
	}
	if got := getFloatEnvInRange("NOTIFIER_BREAKER_OPEN_JITTER", 0.2, 0, 1); got != 0.2 {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_JITTER fallback = %v, want 0.2", got)
	}
}

func TestNotifierEnvParsing_UsesValidValues(t *testing.T) {
	t.Setenv("NOTIFIER_MAX_RETRIES", "8")
	t.Setenv("NOTIFIER_RETRY_BASE_WAIT", "750ms")
	t.Setenv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", "5")
	t.Setenv("NOTIFIER_BREAKER_OPEN_INTERVAL", "45s")
	t.Setenv("NOTIFIER_BREAKER_OPEN_JITTER", "0.35")

	if got := getPositiveIntEnv("NOTIFIER_MAX_RETRIES", 3); got != 8 {
		t.Fatalf("NOTIFIER_MAX_RETRIES parsed = %d, want 8", got)
	}
	if got := getDurationEnv("NOTIFIER_RETRY_BASE_WAIT", time.Second); got != 750*time.Millisecond {
		t.Fatalf("NOTIFIER_RETRY_BASE_WAIT parsed = %v, want 750ms", got)
	}
	if got := getPositiveIntEnv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", 3); got != 5 {
		t.Fatalf("NOTIFIER_BREAKER_FAILURE_THRESHOLD parsed = %d, want 5", got)
	}
	if got := getDurationEnv("NOTIFIER_BREAKER_OPEN_INTERVAL", 30*time.Second); got != 45*time.Second {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_INTERVAL parsed = %v, want 45s", got)
	}
	if got := getFloatEnvInRange("NOTIFIER_BREAKER_OPEN_JITTER", 0.2, 0, 1); got != 0.35 {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_JITTER parsed = %v, want 0.35", got)
	}
}

func TestNotifierEnvParsing_EmptyValueUsesFallback(t *testing.T) {
	const key = "NOTIFIER_MAX_RETRIES"
	_ = os.Unsetenv(key)
	if got := getPositiveIntEnv(key, 4); got != 4 {
		t.Fatalf("empty env must use fallback, got %d", got)
	}
}
