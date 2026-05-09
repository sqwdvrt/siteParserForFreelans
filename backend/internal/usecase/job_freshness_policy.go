package usecase

import (
	"strings"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

const kworkNotificationMaxUnseen = 6 * time.Hour

func notificationJobIsFresh(job *domain.Job, now time.Time) bool {
	if job == nil {
		return false
	}
	if now.IsZero() {
		now = time.Now()
	}
	switch strings.ToLower(strings.TrimSpace(job.Source)) {
	case "kwork":
		if job.LastSeenAt.IsZero() {
			return false
		}
		return !job.LastSeenAt.Before(now.Add(-kworkNotificationMaxUnseen))
	default:
		return true
	}
}
