package config

import (
	"testing"
	"time"
)

func TestParsePositiveIntEnv(t *testing.T) {
	const key = "TEST_PARSE_POSITIVE_INT_ENV"

	t.Setenv(key, "")
	if got, err := ParsePositiveIntEnv(key, 7); err != nil || got != 7 {
		t.Fatalf("fallback int = %d, err=%v; want 7, nil", got, err)
	}

	t.Setenv(key, "12")
	if got, err := ParsePositiveIntEnv(key, 7); err != nil || got != 12 {
		t.Fatalf("parsed int = %d, err=%v; want 12, nil", got, err)
	}

	t.Setenv(key, "0")
	if _, err := ParsePositiveIntEnv(key, 7); err == nil {
		t.Fatal("expected error for zero int env")
	}
}

func TestParsePositiveDurationEnv(t *testing.T) {
	const key = "TEST_PARSE_POSITIVE_DURATION_ENV"

	t.Setenv(key, "")
	if got, err := ParsePositiveDurationEnv(key, 5*time.Minute); err != nil || got != 5*time.Minute {
		t.Fatalf("fallback duration = %v, err=%v; want 5m, nil", got, err)
	}

	t.Setenv(key, "45s")
	if got, err := ParsePositiveDurationEnv(key, time.Minute); err != nil || got != 45*time.Second {
		t.Fatalf("parsed duration = %v, err=%v; want 45s, nil", got, err)
	}

	t.Setenv(key, "bad")
	if _, err := ParsePositiveDurationEnv(key, time.Minute); err == nil {
		t.Fatal("expected error for invalid duration env")
	}
}

func TestParseFloatEnvInRange(t *testing.T) {
	const key = "TEST_PARSE_FLOAT_ENV_IN_RANGE"

	t.Setenv(key, "")
	if got, err := ParseFloatEnvInRange(key, 0.25, 0, 1); err != nil || got != 0.25 {
		t.Fatalf("fallback float = %v, err=%v; want 0.25, nil", got, err)
	}

	t.Setenv(key, "0.75")
	if got, err := ParseFloatEnvInRange(key, 0.25, 0, 1); err != nil || got != 0.75 {
		t.Fatalf("parsed float = %v, err=%v; want 0.75, nil", got, err)
	}

	t.Setenv(key, "1.5")
	if _, err := ParseFloatEnvInRange(key, 0.25, 0, 1); err == nil {
		t.Fatal("expected error for out-of-range float env")
	}
}
