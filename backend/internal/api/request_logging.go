package api

import (
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
)

// RequestLoggingMiddleware logs HTTP request/response metadata in structured form.
func RequestLoggingMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
	if logger == nil {
		logger = slog.Default()
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			startedAt := time.Now()
			rec := &requestLogStatusRecorder{ResponseWriter: w}
			next.ServeHTTP(rec, r)

			status := rec.status
			if status == 0 {
				status = http.StatusOK
			}
			route := routePattern(r)
			level := slog.LevelInfo
			if status >= http.StatusInternalServerError {
				level = slog.LevelError
			} else if status >= http.StatusBadRequest {
				level = slog.LevelWarn
			}

			attrs := []slog.Attr{
				slog.String("method", strings.ToUpper(strings.TrimSpace(r.Method))),
				slog.String("route", route),
				slog.Int("status", status),
				slog.Duration("duration", time.Since(startedAt)),
				slog.Int("response_bytes", rec.bytesWritten),
			}
			if ip := parseRemoteIP(r.RemoteAddr); ip != nil {
				attrs = append(attrs, slog.String("client_ip", ip.String()))
			}
			if ua := strings.TrimSpace(r.UserAgent()); ua != "" {
				attrs = append(attrs, slog.String("user_agent", ua))
			}
			if reqID := strings.TrimSpace(r.Header.Get("X-Request-ID")); reqID != "" {
				attrs = append(attrs, slog.String("request_id", reqID))
			}

			logger.LogAttrs(r.Context(), level, "http request", attrs...)
		})
	}
}

type requestLogStatusRecorder struct {
	http.ResponseWriter
	status       int
	bytesWritten int
}

func (s *requestLogStatusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func (s *requestLogStatusRecorder) Write(b []byte) (int, error) {
	if s.status == 0 {
		s.status = http.StatusOK
	}
	n, err := s.ResponseWriter.Write(b)
	s.bytesWritten += n
	return n, err
}

func routePattern(r *http.Request) string {
	if r == nil {
		return "/"
	}
	if routeCtx := chi.RouteContext(r.Context()); routeCtx != nil {
		if pattern := strings.TrimSpace(routeCtx.RoutePattern()); pattern != "" {
			return pattern
		}
	}
	if r.URL != nil {
		if p := strings.TrimSpace(r.URL.Path); p != "" {
			return p
		}
	}
	return "/"
}
