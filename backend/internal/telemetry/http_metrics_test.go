package telemetry

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
)

func TestHTTPMetricsMiddleware_CollectsRequestMetrics(t *testing.T) {
	reg := prometheus.NewRegistry()
	metrics := NewHTTPMetrics(reg)

	r := chi.NewRouter()
	r.Use(metrics.Middleware)
	r.Get("/users/{id}", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})

	req := httptest.NewRequest(http.MethodGet, "/users/42", nil)
	rr := httptest.NewRecorder()
	r.ServeHTTP(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("want status %d, got %d", http.StatusNoContent, rr.Code)
	}

	mf, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	requests := findMetricFamily(t, mf, httpRequestsTotalMetricName)
	assertCounterLabelValue(
		t,
		requests.GetMetric(),
		map[string]string{
			"method": "GET",
			"route":  "/users/{id}",
			"status": "204",
		},
		1,
	)

	duration := findMetricFamily(t, mf, httpRequestDurationMetricName)
	if len(duration.GetMetric()) == 0 {
		t.Fatalf("expected duration metrics, got none")
	}
}

func findMetricFamily(t *testing.T, families []*dto.MetricFamily, name string) *dto.MetricFamily {
	t.Helper()
	for _, f := range families {
		if f.GetName() == name {
			return f
		}
	}
	t.Fatalf("metric family %q not found", name)
	return nil
}

func assertCounterLabelValue(
	t *testing.T,
	metrics []*dto.Metric,
	wantLabels map[string]string,
	wantValue float64,
) {
	t.Helper()
	for _, m := range metrics {
		if labelsMatch(m.GetLabel(), wantLabels) {
			if m.GetCounter() == nil {
				t.Fatalf("expected counter metric for labels %v", wantLabels)
			}
			got := m.GetCounter().GetValue()
			if got != wantValue {
				t.Fatalf("counter value mismatch for labels %v: want %v, got %v", wantLabels, wantValue, got)
			}
			return
		}
	}
	t.Fatalf("metric with labels %v not found", wantLabels)
}

func labelsMatch(labels []*dto.LabelPair, want map[string]string) bool {
	if len(labels) != len(want) {
		return false
	}
	for _, pair := range labels {
		name := pair.GetName()
		value := pair.GetValue()
		if want[name] != value {
			return false
		}
	}
	return true
}
