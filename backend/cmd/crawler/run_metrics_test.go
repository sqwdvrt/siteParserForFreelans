package main

import (
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
)

func TestObserveCrawlerRunOutcome_PartialFailureCountsSingleFailedRun(t *testing.T) {
	reg := prometheus.NewRegistry()
	metrics := telemetry.NewCrawlerMetrics(reg, "ai-process")

	observeCrawlerRunOutcome(metrics, crawlRunSummary{
		totalSaved:     3,
		hadFailures:    true,
		wasInterrupted: false,
	}, 2*time.Second)

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	runs := findMetricFamilyByName(t, families, "siteparser_crawler_runs_total")
	assertCounterValue(t, runs.GetMetric(), map[string]string{"status": "success"}, 0)
	assertCounterValue(t, runs.GetMetric(), map[string]string{"status": "failed"}, 1)
	assertCounterValue(t, runs.GetMetric(), map[string]string{"status": "interrupted"}, 0)

	saved := findMetricFamilyByName(t, families, "siteparser_crawler_saved_jobs_total")
	if got := saved.GetMetric()[0].GetCounter().GetValue(); got != 3 {
		t.Fatalf("saved jobs counter = %v, want 3", got)
	}

	scraped := findMetricFamilyByName(t, families, "siteparser_crawler_jobs_scraped_total")
	if got := scraped.GetMetric()[0].GetCounter().GetValue(); got != 3 {
		t.Fatalf("scraped jobs counter = %v, want 3", got)
	}
}

func findMetricFamilyByName(t *testing.T, families []*dto.MetricFamily, name string) *dto.MetricFamily {
	t.Helper()
	for _, family := range families {
		if family.GetName() == name {
			return family
		}
	}
	t.Fatalf("metric family %q not found", name)
	return nil
}

func assertCounterValue(t *testing.T, metrics []*dto.Metric, wantLabels map[string]string, wantValue float64) {
	t.Helper()
	for _, metric := range metrics {
		if metricLabelsMatch(metric.GetLabel(), wantLabels) {
			if metric.GetCounter() == nil {
				t.Fatalf("expected counter metric for labels %v", wantLabels)
			}
			got := metric.GetCounter().GetValue()
			if got != wantValue {
				t.Fatalf("counter value mismatch for labels %v: want %v, got %v", wantLabels, wantValue, got)
			}
			return
		}
	}
	t.Fatalf("metric with labels %v not found", wantLabels)
}

func metricLabelsMatch(labels []*dto.LabelPair, want map[string]string) bool {
	if len(labels) != len(want) {
		return false
	}
	for _, pair := range labels {
		if want[pair.GetName()] != pair.GetValue() {
			return false
		}
	}
	return true
}
