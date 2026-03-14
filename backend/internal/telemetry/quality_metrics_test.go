package telemetry

import (
	"context"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type fakeFeedbackMetricsRepo struct {
	stats port.FeedbackStats
	err   error
}

func (f fakeFeedbackMetricsRepo) Upsert(context.Context, int64, int64, domain.FeedbackType) error {
	return nil
}

func (f fakeFeedbackMetricsRepo) StatsRecent(context.Context, int64, time.Duration) (port.FeedbackStats, error) {
	return port.FeedbackStats{}, nil
}

func (f fakeFeedbackMetricsRepo) GlobalStatsRecent(context.Context, time.Duration) (port.FeedbackStats, error) {
	return f.stats, f.err
}

func TestFeedbackQualityCollector_CollectsRatioAndTotal(t *testing.T) {
	reg := prometheus.NewRegistry()
	reg.MustRegister(NewFeedbackQualityCollector(
		fakeFeedbackMetricsRepo{stats: port.FeedbackStats{GoodCount: 6, BadCount: 4}},
		24*time.Hour,
		time.Second,
		nil,
	))

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	ratio := findMetricFamilyByName(t, families, feedbackPositiveRatioMetricName)
	if got := ratio.GetMetric()[0].GetGauge().GetValue(); got != 0.6 {
		t.Fatalf("feedback positive ratio = %v, want 0.6", got)
	}
	total := findMetricFamilyByName(t, families, feedbackRecentTotalMetricName)
	if got := total.GetMetric()[0].GetGauge().GetValue(); got != 10 {
		t.Fatalf("feedback recent total = %v, want 10", got)
	}
}

func TestFeedbackQualityCollector_ReturnsZeroWhenNoFeedback(t *testing.T) {
	reg := prometheus.NewRegistry()
	reg.MustRegister(NewFeedbackQualityCollector(
		fakeFeedbackMetricsRepo{},
		24*time.Hour,
		time.Second,
		nil,
	))

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	ratio := findMetricFamilyByName(t, families, feedbackPositiveRatioMetricName)
	if got := ratio.GetMetric()[0].GetGauge().GetValue(); got != 0 {
		t.Fatalf("feedback positive ratio = %v, want 0", got)
	}
	total := findMetricFamilyByName(t, families, feedbackRecentTotalMetricName)
	if got := total.GetMetric()[0].GetGauge().GetValue(); got != 0 {
		t.Fatalf("feedback recent total = %v, want 0", got)
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
