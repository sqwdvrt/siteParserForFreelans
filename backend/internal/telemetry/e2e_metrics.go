package telemetry

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

const (
	e2eLatencyHistogramName   = "siteparser_e2e_latency_seconds"
	stageLatencyHistogramName = "siteparser_stage_latency_seconds"
)

// E2ELatencyMetrics отслеживает end-to-end latency от crawl до notification.
type E2ELatencyMetrics struct {
	e2eLatency   *prometheus.HistogramVec
	stageLatency *prometheus.HistogramVec
}

// NewE2ELatencyMetrics создаёт метрики для отслеживания E2E latency.
// e2eLatency: полное время от создания job до отправки notification
// stageLatency: время на каждом этапе pipeline (crawl→queue, queue→AI, AI→match, match→notify)
func NewE2ELatencyMetrics(reg prometheus.Registerer) *E2ELatencyMetrics {
	m := &E2ELatencyMetrics{
		e2eLatency: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{
				Name:    e2eLatencyHistogramName,
				Help:    "End-to-end latency from job creation to notification delivery.",
				Buckets: []float64{60, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400}, // 1m to 24h
			},
			[]string{"source"}, // источник: kwork, flru, и т.д.
		),
		stageLatency: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{
				Name:    stageLatencyHistogramName,
				Help:    "Latency of individual pipeline stages.",
				Buckets: []float64{10, 30, 60, 120, 300, 600, 1800, 3600, 7200}, // 10s to 2h
			},
			[]string{"stage", "source"}, // stage: crawl_to_queue, queue_to_ai, ai_to_match, match_to_notify
		),
	}

	reg.MustRegister(m.e2eLatency, m.stageLatency)
	return m
}

// ObserveE2ELatency записывает полное время от crawl до notification.
func (m *E2ELatencyMetrics) ObserveE2ELatency(source string, latency time.Duration) {
	m.e2eLatency.WithLabelValues(source).Observe(latency.Seconds())
}

// ObserveStageLatency записывает время конкретного этапа pipeline.
func (m *E2ELatencyMetrics) ObserveStageLatency(stage, source string, latency time.Duration) {
	m.stageLatency.WithLabelValues(stage, source).Observe(latency.Seconds())
}

// Stage constants for stageLatency label
const (
	StageCrawlToQueue  = "crawl_to_queue"
	StageQueueToAI     = "queue_to_ai"
	StageAIToMatch     = "ai_to_match"
	StageMatchToNotify = "match_to_notify"
)
