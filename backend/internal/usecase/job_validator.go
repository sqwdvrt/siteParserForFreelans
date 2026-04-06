package usecase

import (
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// JobValidationErrors collects all validation issues for a single job.
type JobValidationErrors []string

// Error implements the error interface.
func (e JobValidationErrors) Error() string {
	return fmt.Sprintf("job validation failed: %s", strings.Join(e, "; "))
}

// ValidateJob checks that a scraped job meets minimum quality requirements.
// This prevents broken extractors from polluting the database with invalid data.
func ValidateJob(job *domain.Job) JobValidationErrors {
	var errs JobValidationErrors

	if job == nil {
		return JobValidationErrors{"job is nil"}
	}
	if job.Source == "" {
		errs = append(errs, "source is empty")
	}
	if job.URL == "" {
		errs = append(errs, "url is empty")
	}
	if job.Title == "" {
		errs = append(errs, "title is empty")
	} else if utf8.RuneCountInString(job.Title) < 5 {
		errs = append(errs, fmt.Sprintf("title too short (%d chars, min 5)", utf8.RuneCountInString(job.Title)))
	} else if utf8.RuneCountInString(job.Title) > 300 {
		errs = append(errs, fmt.Sprintf("title too long (%d chars, max 300)", utf8.RuneCountInString(job.Title)))
	}

	// Description validation — allow empty but warn if title present but desc missing
	if job.Description == "" && job.RawHTML == "" {
		errs = append(errs, "both description and raw_html are empty")
	}

	// RawHTML should be present (it's NOT NULL in DB)
	if job.RawHTML == "" {
		errs = append(errs, "raw_html is empty (required for DB NOT NULL constraint)")
	}

	// Skills should be present but not required
	// Budget is optional

	return errs
}

// ValidationFailureRate tracks the ratio of invalid jobs detected during crawling.
type ValidationFailureRate struct {
	Total    int
	Rejected int
}

// Rate returns the rejection rate as a float 0.0–1.0.
func (r *ValidationFailureRate) Rate() float64 {
	if r.Total == 0 {
		return 0.0
	}
	return float64(r.Rejected) / float64(r.Total)
}
