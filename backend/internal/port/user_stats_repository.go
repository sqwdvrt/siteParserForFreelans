package port

import (
	"context"
	"time"
)

// UserStats — explainable user-facing matching statistics for a fixed time window.
type UserStats struct {
	PeriodDays               int        `json:"period_days"`
	ProjectsFound            int64      `json:"projects_found"`
	ProjectsShown            int64      `json:"projects_shown"`
	ProjectsFilteredOther    int64      `json:"projects_filtered_other"`
	ProjectsFilteredByBudget int64      `json:"projects_filtered_by_budget"`
	ProjectsHiddenByCap      int64      `json:"projects_hidden_by_cap"`
	BudgetFilterActive       bool       `json:"budget_filter_active"`
	Plan                     string     `json:"plan,omitempty"`
	DailyCap                 int        `json:"daily_cap,omitempty"`
	ProExpiresAt             *time.Time `json:"pro_expires_at,omitempty"`
	NotifyHour               *int16     `json:"notify_hour,omitempty"`
}

// UserStatsRepository returns user-facing stats used by /stats command.
type UserStatsRepository interface {
	GetUserStats(ctx context.Context, userID int64, window time.Duration) (*UserStats, error)
}
