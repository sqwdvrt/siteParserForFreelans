package domain

import "time"

// UserPreferences — явные пользовательские предпочтения для персонализации.
type UserPreferences struct {
	UserID           int64
	IncludeKeywords  []string
	ExcludeKeywords  []string
	MinBudget        *float64
	MaxBudget        *float64
	PreferredSources []string
	UpdatedAt        time.Time
}
