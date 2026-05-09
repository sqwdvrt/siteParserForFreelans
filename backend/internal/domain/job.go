package domain

import "time"

type Job struct {
	ID          int64
	Source      string
	URL         string
	ExternalID  string
	Title       string
	Description string
	Budget      string
	Skills      []string
	PostedAt    *time.Time
	LastSeenAt  time.Time
	RawHTML     string
	CreatedAt   time.Time
}
