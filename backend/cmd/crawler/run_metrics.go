package main

import (
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
)

type crawlRunSummary struct {
	totalSaved     int
	hadFailures    bool
	wasInterrupted bool
}

func observeCrawlerRunOutcome(metrics *telemetry.CrawlerMetrics, summary crawlRunSummary, elapsed time.Duration) {
	switch {
	case summary.wasInterrupted:
		metrics.ObserveSavedJobs(summary.totalSaved)
		metrics.ObserveRunInterrupted(elapsed)
	case summary.hadFailures:
		metrics.ObserveSavedJobs(summary.totalSaved)
		metrics.ObserveRunFailure(elapsed)
	default:
		metrics.ObserveRunSuccess(summary.totalSaved, elapsed)
	}
}
