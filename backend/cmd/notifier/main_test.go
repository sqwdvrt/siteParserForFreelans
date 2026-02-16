package main

import (
	"context"
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
