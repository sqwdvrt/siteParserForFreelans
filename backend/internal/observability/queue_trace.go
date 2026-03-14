package observability

import (
	"context"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
)

func QueueDispatchTraceFromContext(ctx context.Context) port.QueueDispatchTrace {
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	return port.QueueDispatchTrace{
		TraceID:     TraceIDFromContext(ctx),
		Traceparent: carrier.Get("traceparent"),
	}
}

func ContextWithQueueDispatchTrace(ctx context.Context, trace port.QueueDispatchTrace) context.Context {
	if ctx == nil {
		ctx = context.Background()
	}
	if trace.TraceID != "" {
		ctx = WithTraceID(ctx, trace.TraceID)
	}
	if trace.Traceparent != "" {
		ctx = otel.GetTextMapPropagator().Extract(ctx, propagation.MapCarrier{
			"traceparent": trace.Traceparent,
		})
	}
	return ctx
}
