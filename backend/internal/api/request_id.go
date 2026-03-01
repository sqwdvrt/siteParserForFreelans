package api

import (
	"net/http"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
)

// RequestIDMiddleware ensures every request has X-Request-ID and propagates it via context/response header.
func RequestIDMiddleware() func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			reqID := observability.NormalizeTraceID(r.Header.Get(observability.HeaderRequestID))
			if reqID == "" {
				reqID = observability.NewTraceID()
			}
			r.Header.Set(observability.HeaderRequestID, reqID)
			w.Header().Set(observability.HeaderRequestID, reqID)
			next.ServeHTTP(w, r.WithContext(observability.WithTraceID(r.Context(), reqID)))
		})
	}
}
