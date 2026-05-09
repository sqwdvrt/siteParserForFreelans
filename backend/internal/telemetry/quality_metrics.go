package telemetry

import (
	"context"
	"log/slog"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const (
	feedbackPositiveRatioMetricName = "siteparser_feedback_positive_ratio"
	feedbackRecentTotalMetricName   = "siteparser_feedback_recent_total"
)

// FeedbackQualityCollector exports recent feedback quality gauges derived from PostgreSQL.
type FeedbackQualityCollector struct {
	repo         port.FeedbackRepository
	window       time.Duration
	queryTimeout time.Duration
	logger       *slog.Logger
	ratioDesc    *prometheus.Desc
	totalDesc    *prometheus.Desc
}

func NewFeedbackQualityCollector(
	repo port.FeedbackRepository,
	window time.Duration,
	queryTimeout time.Duration,
	logger *slog.Logger,
) *FeedbackQualityCollector {
	if window <= 0 {
		window = 7 * 24 * time.Hour
	}
	if queryTimeout <= 0 {
		queryTimeout = 3 * time.Second
	}
	if logger == nil {
		logger = slog.Default()
	}
	return &FeedbackQualityCollector{
		repo:         repo,
		window:       window,
		queryTimeout: queryTimeout,
		logger:       logger,
		ratioDesc: prometheus.NewDesc(
			feedbackPositiveRatioMetricName,
			"Share of positive feedback among recent explicit feedback events.",
			nil,
			nil,
		),
		totalDesc: prometheus.NewDesc(
			feedbackRecentTotalMetricName,
			"Total explicit feedback events observed in the recent quality window.",
			nil,
			nil,
		),
	}
}

func (c *FeedbackQualityCollector) Describe(ch chan<- *prometheus.Desc) {
	ch <- c.ratioDesc
	ch <- c.totalDesc
}

func (c *FeedbackQualityCollector) Collect(ch chan<- prometheus.Metric) {
	if c.repo == nil {
		return
	}

	stats := port.FeedbackStats{}
	ctx, cancel := context.WithTimeout(context.Background(), c.queryTimeout)
	defer cancel()

	loadedStats, err := c.repo.GlobalStatsRecent(ctx, c.window)
	if err != nil {
		c.logger.Warn("feedback quality metrics query failed", "err", err)
	} else {
		stats = loadedStats
	}

	total := float64(stats.GoodCount + stats.BadCount)
	ratio := 0.0
	if total > 0 {
		ratio = float64(stats.GoodCount) / total
	}

	ch <- prometheus.MustNewConstMetric(c.ratioDesc, prometheus.GaugeValue, ratio)
	ch <- prometheus.MustNewConstMetric(c.totalDesc, prometheus.GaugeValue, total)
}
