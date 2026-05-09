package observability

import (
	"context"
	"testing"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
)

func TestQueueDispatchTraceRoundTrip(t *testing.T) {
	previous := otel.GetTextMapPropagator()
	otel.SetTextMapPropagator(propagation.TraceContext{})
	defer otel.SetTextMapPropagator(previous)

	ctx := WithTraceID(context.Background(), "trace-123")
	trace := QueueDispatchTraceFromContext(ctx)
	if trace.TraceID != "trace-123" {
		t.Fatalf("trace_id=%q want trace-123", trace.TraceID)
	}

	var nilCtx context.Context
	restored := ContextWithQueueDispatchTrace(nilCtx, trace)
	if got := TraceIDFromContext(restored); got != "trace-123" {
		t.Fatalf("restored trace_id=%q want trace-123", got)
	}
}

func TestContextWithQueueDispatchTrace_NoData(t *testing.T) {
	var nilCtx context.Context
	ctx := ContextWithQueueDispatchTrace(nilCtx, port.QueueDispatchTrace{})
	if ctx == nil {
		t.Fatal("context must not be nil")
	}
	if got := TraceIDFromContext(ctx); got != "" {
		t.Fatalf("trace_id=%q want empty", got)
	}
}
