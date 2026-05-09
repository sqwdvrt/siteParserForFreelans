package telemetry

import (
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
)

const (
	httpRequestsTotalMetricName   = "siteparser_http_requests_total"
	httpRequestDurationMetricName = "siteparser_http_request_duration_seconds"
	httpInflightMetricName        = "siteparser_http_requests_inflight"
)

type HTTPMetrics struct {
	requestsTotal   *prometheus.CounterVec
	requestDuration *prometheus.HistogramVec
	inflight        prometheus.Gauge
}

func NewHTTPMetrics(reg prometheus.Registerer) *HTTPMetrics {
	m := &HTTPMetrics{
		requestsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: httpRequestsTotalMetricName,
				Help: "Total number of HTTP requests served by backend API.",
			},
			[]string{"method", "route", "status"},
		),
		requestDuration: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{
				Name:    httpRequestDurationMetricName,
				Help:    "Duration of HTTP requests served by backend API.",
				Buckets: []float64{0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10},
			},
			[]string{"method", "route"},
		),
		inflight: prometheus.NewGauge(
			prometheus.GaugeOpts{
				Name: httpInflightMetricName,
				Help: "Number of in-flight HTTP requests in backend API.",
			},
		),
	}

	reg.MustRegister(m.requestsTotal, m.requestDuration, m.inflight)
	return m
}

func (m *HTTPMetrics) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		m.inflight.Inc()
		defer m.inflight.Dec()

		rec := &statusRecorder{ResponseWriter: w}
		next.ServeHTTP(rec, r)

		statusCode := rec.status
		if statusCode == 0 {
			statusCode = http.StatusOK
		}

		route := routePattern(r)
		method := r.Method
		if method == "" {
			method = "UNKNOWN"
		}
		m.requestsTotal.WithLabelValues(method, route, strconv.Itoa(statusCode)).Inc()
		m.requestDuration.WithLabelValues(method, route).Observe(time.Since(started).Seconds())
	})
}

func routePattern(r *http.Request) string {
	route := "unknown"
	if rctx := chi.RouteContext(r.Context()); rctx != nil {
		if p := rctx.RoutePattern(); p != "" {
			route = p
		}
	}
	return route
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func (s *statusRecorder) Write(b []byte) (int, error) {
	if s.status == 0 {
		s.status = http.StatusOK
	}
	return s.ResponseWriter.Write(b)
}
