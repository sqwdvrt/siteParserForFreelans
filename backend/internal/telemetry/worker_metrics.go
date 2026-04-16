package telemetry

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

const (
	crawlerRunsTotalMetricName           = "siteparser_crawler_runs_total"
	crawlerRunDurationMetricName         = "siteparser_crawler_run_duration_seconds"
	crawlerSavedJobsTotalMetricName      = "siteparser_crawler_saved_jobs_total"
	crawlerJobsScrapedTotalMetricName    = "siteparser_crawler_jobs_scraped_total"
	crawlerJobsFilteredTotalMetricName   = "siteparser_crawler_jobs_filtered_total"
	crawlerQueueDepthMetricName          = "siteparser_crawler_queue_depth"
	notifierNotificationsMetricName   = "siteparser_notifier_notifications_total"
	notifierQueueDepthMetricName      = "siteparser_notifier_queue_depth"
	queueStateReady                   = "ready"
	queueStateProcessing              = "processing"
	queueStateDLQ                     = "dlq"
	crawlerRunStatusSuccess           = "success"
	crawlerRunStatusFailed            = "failed"
	crawlerRunStatusInterrupted       = "interrupted"
	notifierNotificationStatusSent    = "sent"
	notifierNotificationStatusFailed  = "failed"
)

type CrawlerMetrics struct {
	queueName    string
	runsTotal    *prometheus.CounterVec
	runDuration  *prometheus.HistogramVec
	savedJobs    prometheus.Counter
	scrapedJobs  prometheus.Counter
	filteredJobs *prometheus.CounterVec
	queueDepth   *prometheus.GaugeVec
}

func NewCrawlerMetrics(reg prometheus.Registerer, queueName string) *CrawlerMetrics {
	if queueName == "" {
		queueName = "ai-process"
	}
	m := &CrawlerMetrics{
		queueName: queueName,
		runsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: crawlerRunsTotalMetricName,
				Help: "Total number of crawler runs by result status.",
			},
			[]string{"status"},
		),
		runDuration: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{
				Name:    crawlerRunDurationMetricName,
				Help:    "Duration of crawler runs by result status.",
				Buckets: []float64{0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120},
			},
			[]string{"status"},
		),
		savedJobs: prometheus.NewCounter(
			prometheus.CounterOpts{
				Name: crawlerSavedJobsTotalMetricName,
				Help: "Total number of jobs saved by crawler runs.",
			},
		),
		scrapedJobs: prometheus.NewCounter(
			prometheus.CounterOpts{
				Name: crawlerJobsScrapedTotalMetricName,
				Help: "Total number of jobs scraped by crawler runs.",
			},
		),
		filteredJobs: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: crawlerJobsFilteredTotalMetricName,
				Help: "Total number of jobs filtered out by the crawler before saving.",
			},
			[]string{"source", "reason"},
		),
		queueDepth: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Name: crawlerQueueDepthMetricName,
				Help: "Current depth of crawler target queue and its processing/DLQ lists.",
			},
			[]string{"queue", "state"},
		),
	}

	reg.MustRegister(m.runsTotal, m.runDuration, m.savedJobs, m.scrapedJobs, m.filteredJobs, m.queueDepth)
	m.runsTotal.WithLabelValues(crawlerRunStatusSuccess).Add(0)
	m.runsTotal.WithLabelValues(crawlerRunStatusFailed).Add(0)
	m.runsTotal.WithLabelValues(crawlerRunStatusInterrupted).Add(0)
	m.queueDepth.WithLabelValues(m.queueName, queueStateReady).Set(0)
	m.queueDepth.WithLabelValues(m.queueName, queueStateProcessing).Set(0)
	m.queueDepth.WithLabelValues(m.queueName, queueStateDLQ).Set(0)
	return m
}

func (m *CrawlerMetrics) ObserveRunSuccess(saved int, elapsed time.Duration) {
	m.runsTotal.WithLabelValues(crawlerRunStatusSuccess).Inc()
	m.runDuration.WithLabelValues(crawlerRunStatusSuccess).Observe(elapsed.Seconds())
	m.ObserveSavedJobs(saved)
}

func (m *CrawlerMetrics) ObserveSavedJobs(saved int) {
	if saved > 0 {
		m.savedJobs.Add(float64(saved))
		m.scrapedJobs.Add(float64(saved))
	}
}

func (m *CrawlerMetrics) ObserveRunFailure(elapsed time.Duration) {
	m.runsTotal.WithLabelValues(crawlerRunStatusFailed).Inc()
	m.runDuration.WithLabelValues(crawlerRunStatusFailed).Observe(elapsed.Seconds())
}

func (m *CrawlerMetrics) ObserveFiltered(source, reason string) {
	m.filteredJobs.WithLabelValues(source, reason).Inc()
}

func (m *CrawlerMetrics) ObserveRunInterrupted(elapsed time.Duration) {
	m.runsTotal.WithLabelValues(crawlerRunStatusInterrupted).Inc()
	m.runDuration.WithLabelValues(crawlerRunStatusInterrupted).Observe(elapsed.Seconds())
}

func (m *CrawlerMetrics) SetQueueDepth(ready int64, processing int64, dlq int64) {
	m.queueDepth.WithLabelValues(m.queueName, queueStateReady).Set(float64(ready))
	m.queueDepth.WithLabelValues(m.queueName, queueStateProcessing).Set(float64(processing))
	m.queueDepth.WithLabelValues(m.queueName, queueStateDLQ).Set(float64(dlq))
}

type NotifierMetrics struct {
	queueName     string
	notifications *prometheus.CounterVec
	queueDepth    *prometheus.GaugeVec
}

func NewNotifierMetrics(reg prometheus.Registerer, queueName string) *NotifierMetrics {
	if queueName == "" {
		queueName = "match-notify"
	}
	m := &NotifierMetrics{
		queueName: queueName,
		notifications: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: notifierNotificationsMetricName,
				Help: "Total notifier delivery attempts by status.",
			},
			[]string{"status"},
		),
		queueDepth: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Name: notifierQueueDepthMetricName,
				Help: "Current depth of notifier queue and its processing/DLQ lists.",
			},
			[]string{"queue", "state"},
		),
	}

	reg.MustRegister(m.notifications, m.queueDepth)
	m.notifications.WithLabelValues(notifierNotificationStatusSent).Add(0)
	m.notifications.WithLabelValues(notifierNotificationStatusFailed).Add(0)
	m.queueDepth.WithLabelValues(m.queueName, queueStateReady).Set(0)
	m.queueDepth.WithLabelValues(m.queueName, queueStateProcessing).Set(0)
	m.queueDepth.WithLabelValues(m.queueName, queueStateDLQ).Set(0)
	return m
}

func (m *NotifierMetrics) ObserveSent() {
	m.notifications.WithLabelValues(notifierNotificationStatusSent).Inc()
}

func (m *NotifierMetrics) ObserveFailed() {
	m.notifications.WithLabelValues(notifierNotificationStatusFailed).Inc()
}

func (m *NotifierMetrics) SetQueueDepth(ready int64, processing int64, dlq int64) {
	m.queueDepth.WithLabelValues(m.queueName, queueStateReady).Set(float64(ready))
	m.queueDepth.WithLabelValues(m.queueName, queueStateProcessing).Set(float64(processing))
	m.queueDepth.WithLabelValues(m.queueName, queueStateDLQ).Set(float64(dlq))
}
