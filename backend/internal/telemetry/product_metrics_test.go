package telemetry

import (
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
)

func TestProductMetrics_RecordEventAndConversion(t *testing.T) {
	reg := prometheus.NewRegistry()
	metrics := NewProductMetrics(reg)

	metrics.RecordEvent("pro_upgrade_requested", "telegram")
	metrics.SetConversionRate("upgrade_funnel", 0.42)

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("Gather err=%v", err)
	}
	if got := counterValue(families, productEventsTotalName, "event_type", "pro_upgrade_requested", "source", "telegram"); got != 1 {
		t.Fatalf("eventsTotal=%v want 1", got)
	}
	if got := gaugeValue(families, productConversionName, "funnel", "upgrade_funnel"); got != 0.42 {
		t.Fatalf("conversionRate=%v want 0.42", got)
	}
}

func TestE2ELatencyMetrics_Observe(t *testing.T) {
	reg := prometheus.NewRegistry()
	metrics := NewE2ELatencyMetrics(reg)

	metrics.ObserveE2ELatency("kwork", 2*time.Minute)
	metrics.ObserveStageLatency(StageMatchToNotify, "kwork", 30*time.Second)

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("Gather err=%v", err)
	}
	if got := histogramCount(families, e2eLatencyHistogramName, "source", "kwork"); got != 1 {
		t.Fatalf("e2e metric count=%d want 1", got)
	}
	if got := histogramCount(families, stageLatencyHistogramName, "stage", StageMatchToNotify, "source", "kwork"); got != 1 {
		t.Fatalf("stage metric count=%d want 1", got)
	}
}

func counterValue(families []*dto.MetricFamily, name string, labels ...string) float64 {
	for _, family := range families {
		if family.GetName() != name {
			continue
		}
		for _, metric := range family.GetMetric() {
			if labelPairsMatch(metric.GetLabel(), labels...) && metric.Counter != nil {
				return metric.GetCounter().GetValue()
			}
		}
	}
	return 0
}

func gaugeValue(families []*dto.MetricFamily, name string, labels ...string) float64 {
	for _, family := range families {
		if family.GetName() != name {
			continue
		}
		for _, metric := range family.GetMetric() {
			if labelPairsMatch(metric.GetLabel(), labels...) && metric.Gauge != nil {
				return metric.GetGauge().GetValue()
			}
		}
	}
	return 0
}

func histogramCount(families []*dto.MetricFamily, name string, labels ...string) uint64 {
	for _, family := range families {
		if family.GetName() != name {
			continue
		}
		for _, metric := range family.GetMetric() {
			if labelPairsMatch(metric.GetLabel(), labels...) && metric.Histogram != nil {
				return metric.GetHistogram().GetSampleCount()
			}
		}
	}
	return 0
}

func labelPairsMatch(pairs []*dto.LabelPair, labels ...string) bool {
	if len(labels)%2 != 0 {
		return false
	}
	for i := 0; i < len(labels); i += 2 {
		matched := false
		for _, pair := range pairs {
			if pair.GetName() == labels[i] && pair.GetValue() == labels[i+1] {
				matched = true
				break
			}
		}
		if !matched {
			return false
		}
	}
	return true
}
