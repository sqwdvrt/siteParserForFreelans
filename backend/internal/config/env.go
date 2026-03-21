package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// ParsePositiveIntEnv reads an integer env var that must be > 0.
// Returns fallback when the variable is unset; returns an error on bad values.
func ParsePositiveIntEnv(key string, fallback int) (int, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("%s must be positive integer: %w", key, err)
	}
	if v <= 0 {
		return 0, fmt.Errorf("%s must be > 0", key)
	}
	return v, nil
}

// ParsePositiveDurationEnv reads a duration env var that must be > 0.
// Returns fallback when the variable is unset; returns an error on bad values.
func ParsePositiveDurationEnv(key string, fallback time.Duration) (time.Duration, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := time.ParseDuration(raw)
	if err != nil {
		return 0, fmt.Errorf("%s must be valid duration: %w", key, err)
	}
	if v <= 0 {
		return 0, fmt.Errorf("%s must be > 0", key)
	}
	return v, nil
}

// ParseFloatEnvInRange reads a float64 env var and validates it is within [min, max].
// Returns fallback when the variable is unset; returns an error on bad values.
func ParseFloatEnvInRange(key string, fallback float64, min, max float64) (float64, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return 0, fmt.Errorf("%s must be number: %w", key, err)
	}
	if v < min || v > max {
		return 0, fmt.Errorf("%s must be within [%.2f, %.2f]", key, min, max)
	}
	return v, nil
}
