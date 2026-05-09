package observability

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"strings"
	"sync/atomic"
)

const HeaderRequestID = "X-Request-ID"

const (
	traceIDBytes     = 16
	maxTraceIDLength = 128
)

type traceIDContextKey struct{}

var fallbackCounter atomic.Uint64

func NewTraceID() string {
	buf := make([]byte, traceIDBytes)
	if _, err := rand.Read(buf); err != nil {
		// crypto/rand unavailable: use a monotonic counter so every call
		// still gets a unique ID and correlation is preserved.
		return fmt.Sprintf("fallback-%016x", fallbackCounter.Add(1))
	}
	return hex.EncodeToString(buf)
}

func NormalizeTraceID(raw string) string {
	v := strings.TrimSpace(raw)
	if v == "" {
		return ""
	}
	if len(v) > maxTraceIDLength {
		v = v[:maxTraceIDLength]
	}
	return v
}

func WithTraceID(ctx context.Context, traceID string) context.Context {
	traceID = NormalizeTraceID(traceID)
	if traceID == "" {
		return ctx
	}
	if ctx == nil {
		ctx = context.Background()
	}
	return context.WithValue(ctx, traceIDContextKey{}, traceID)
}

func TraceIDFromContext(ctx context.Context) string {
	if ctx == nil {
		return ""
	}
	if v, ok := ctx.Value(traceIDContextKey{}).(string); ok {
		return NormalizeTraceID(v)
	}
	return ""
}
