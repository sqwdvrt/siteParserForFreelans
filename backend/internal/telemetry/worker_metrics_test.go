package telemetry

import (
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
)

func TestCrawlerMetrics_CollectsRunAndQueueMetrics(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewCrawlerMetrics(reg, "ai-process")

	m.ObserveRunSuccess(3, 2*time.Second)
	m.ObserveRunFailure(1500 * time.Millisecond)
	m.ObserveRunInterrupted(500 * time.Millisecond)
	m.SetQueueDepth(10, 2, 1)

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	runs := findMetricFamily(t, families, crawlerRunsTotalMetricName)
	assertCounterLabelValue(t, runs.GetMetric(), map[string]string{"status": crawlerRunStatusSuccess}, 1)
	assertCounterLabelValue(t, runs.GetMetric(), map[string]string{"status": crawlerRunStatusFailed}, 1)
	assertCounterLabelValue(t, runs.GetMetric(), map[string]string{"status": crawlerRunStatusInterrupted}, 1)

	saved := findMetricFamily(t, families, crawlerSavedJobsTotalMetricName)
	if got := saved.GetMetric()[0].GetCounter().GetValue(); got != 3 {
		t.Fatalf("saved jobs counter = %v, want 3", got)
	}

	queueDepth := findMetricFamily(t, families, crawlerQueueDepthMetricName)
	assertGaugeLabelValue(
		t,
		queueDepth.GetMetric(),
		map[string]string{"queue": "ai-process", "state": queueStateReady},
		10,
	)
	assertGaugeLabelValue(
		t,
		queueDepth.GetMetric(),
		map[string]string{"queue": "ai-process", "state": queueStateProcessing},
		2,
	)
	assertGaugeLabelValue(
		t,
		queueDepth.GetMetric(),
		map[string]string{"queue": "ai-process", "state": queueStateDLQ},
		1,
	)
}

func TestNotifierMetrics_CollectsNotificationAndQueueMetrics(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewNotifierMetrics(reg, "match-notify")

	m.ObserveSent()
	m.ObserveSent()
	m.ObserveFailed()
	m.SetQueueDepth(8, 3, 2)

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	notifications := findMetricFamily(t, families, notifierNotificationsMetricName)
	assertCounterLabelValue(
		t,
		notifications.GetMetric(),
		map[string]string{"status": notifierNotificationStatusSent},
		2,
	)
	assertCounterLabelValue(
		t,
		notifications.GetMetric(),
		map[string]string{"status": notifierNotificationStatusFailed},
		1,
	)

	queueDepth := findMetricFamily(t, families, notifierQueueDepthMetricName)
	assertGaugeLabelValue(
		t,
		queueDepth.GetMetric(),
		map[string]string{"queue": "match-notify", "state": queueStateReady},
		8,
	)
	assertGaugeLabelValue(
		t,
		queueDepth.GetMetric(),
		map[string]string{"queue": "match-notify", "state": queueStateProcessing},
		3,
	)
	assertGaugeLabelValue(
		t,
		queueDepth.GetMetric(),
		map[string]string{"queue": "match-notify", "state": queueStateDLQ},
		2,
	)
}

func assertGaugeLabelValue(
	t *testing.T,
	metrics []*dto.Metric,
	wantLabels map[string]string,
	wantValue float64,
) {
	t.Helper()
	for _, m := range metrics {
		if labelsMatch(m.GetLabel(), wantLabels) {
			if m.GetGauge() == nil {
				t.Fatalf("expected gauge metric for labels %v", wantLabels)
			}
			got := m.GetGauge().GetValue()
			if got != wantValue {
				t.Fatalf("gauge value mismatch for labels %v: want %v, got %v", wantLabels, wantValue, got)
			}
			return
		}
	}
	t.Fatalf("metric with labels %v not found", wantLabels)
}
