package observability

import (
	"context"
	"fmt"
	"strings"
	"testing"
)

func TestNewTraceID_GeneratesHex(t *testing.T) {
	got := NewTraceID()
	if len(got) == 0 {
		t.Fatal("trace id must not be empty")
	}
	if len(got) != 32 {
		t.Fatalf("trace id len=%d, want 32", len(got))
	}
}

func TestNewTraceID_Unique(t *testing.T) {
	seen := make(map[string]struct{}, 100)
	for i := range 100 {
		id := NewTraceID()
		if _, dup := seen[id]; dup {
			t.Fatalf("duplicate trace id at iteration %d: %q", i, id)
		}
		seen[id] = struct{}{}
	}
}

func TestFallbackCounter_Unique(t *testing.T) {
	before := fallbackCounter.Load()
	a := fmt.Sprintf("fallback-%016x", fallbackCounter.Add(1))
	b := fmt.Sprintf("fallback-%016x", fallbackCounter.Add(1))
	if a == b {
		t.Fatalf("fallback IDs must be unique: a=%q b=%q", a, b)
	}
	_ = before
}

func TestNormalizeTraceID_TrimsAndCapsLength(t *testing.T) {
	raw := "  abc  "
	if got := NormalizeTraceID(raw); got != "abc" {
		t.Fatalf("normalize=%q want=abc", got)
	}

	long := strings.Repeat("x", maxTraceIDLength+5)
	if got := NormalizeTraceID(long); len(got) != maxTraceIDLength {
		t.Fatalf("len(normalize(long))=%d want=%d", len(got), maxTraceIDLength)
	}
}

func TestWithAndFromContext(t *testing.T) {
	ctx := WithTraceID(context.Background(), " trace-123 ")
	if got := TraceIDFromContext(ctx); got != "trace-123" {
		t.Fatalf("trace_id=%q want=trace-123", got)
	}
	if got := TraceIDFromContext(context.Background()); got != "" {
		t.Fatalf("trace_id from empty context=%q want empty", got)
	}
}
