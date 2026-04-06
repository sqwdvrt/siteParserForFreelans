package domain

import (
	"errors"
	"fmt"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// HttpStatusError
// ---------------------------------------------------------------------------

func TestHttpStatusError_Error_containsStatusAndURL(t *testing.T) {
	err := &HttpStatusError{StatusCode: 404, URL: "https://example.com/job/1"}
	got := err.Error()
	want := "http 404: https://example.com/job/1"
	if got != want {
		t.Fatalf("Error() = %q, want %q", got, want)
	}
}

func TestHttpStatusError_Error_500(t *testing.T) {
	err := &HttpStatusError{StatusCode: 500, URL: "https://api.example.com"}
	got := err.Error()
	want := "http 500: https://api.example.com"
	if got != want {
		t.Fatalf("Error() = %q, want %q", got, want)
	}
}

func TestIsGone_returns_true_for_404(t *testing.T) {
	err := &HttpStatusError{StatusCode: 404, URL: "https://example.com/job/99"}
	if !IsGone(err) {
		t.Fatal("IsGone should be true for 404")
	}
}

func TestIsGone_returns_true_for_410(t *testing.T) {
	err := &HttpStatusError{StatusCode: 410, URL: "https://example.com/job/99"}
	if !IsGone(err) {
		t.Fatal("IsGone should be true for 410")
	}
}

func TestIsGone_returns_false_for_500(t *testing.T) {
	err := &HttpStatusError{StatusCode: 500, URL: "https://example.com"}
	if IsGone(err) {
		t.Fatal("IsGone should be false for 500")
	}
}

func TestIsGone_returns_false_for_non_http_error(t *testing.T) {
	err := errors.New("connection refused")
	if IsGone(err) {
		t.Fatal("IsGone should be false for non-HttpStatusError")
	}
}

func TestIsGone_returns_false_for_nil_error(t *testing.T) {
	if IsGone(nil) {
		t.Fatal("IsGone should be false for nil")
	}
}

func TestIsGone_unwraps_wrapped_http_status_error(t *testing.T) {
	inner := &HttpStatusError{StatusCode: 404, URL: "https://example.com"}
	wrapped := fmt.Errorf("fetch failed: %w", inner)
	if !IsGone(wrapped) {
		t.Fatal("IsGone should unwrap wrapped HttpStatusError")
	}
}

// ---------------------------------------------------------------------------
// User.IsPaused
// ---------------------------------------------------------------------------

func TestIsPaused_returns_false_when_paused_until_is_nil(t *testing.T) {
	u := &User{ID: 1, TelegramID: 100}
	if u.IsPaused(time.Now()) {
		t.Fatal("IsPaused should be false when PausedUntil is nil")
	}
}

func TestIsPaused_returns_true_when_pause_expires_in_future(t *testing.T) {
	future := time.Now().Add(24 * time.Hour)
	u := &User{ID: 1, TelegramID: 100, PausedUntil: &future}
	if !u.IsPaused(time.Now()) {
		t.Fatal("IsPaused should be true when PausedUntil is in the future")
	}
}

func TestIsPaused_returns_false_when_pause_already_expired(t *testing.T) {
	past := time.Now().Add(-1 * time.Hour)
	u := &User{ID: 1, TelegramID: 100, PausedUntil: &past}
	if u.IsPaused(time.Now()) {
		t.Fatal("IsPaused should be false when PausedUntil is in the past")
	}
}

func TestIsPaused_returns_false_for_nil_user(t *testing.T) {
	var u *User
	if u.IsPaused(time.Now()) {
		t.Fatal("IsPaused should be false for nil user")
	}
}

func TestIsPaused_table(t *testing.T) {
	now := time.Date(2026, 4, 6, 12, 0, 0, 0, time.UTC)
	future := now.Add(1 * time.Hour)
	past := now.Add(-1 * time.Hour)

	tests := []struct {
		name        string
		pausedUntil *time.Time
		want        bool
	}{
		{"nil PausedUntil", nil, false},
		{"PausedUntil in future", &future, true},
		{"PausedUntil in past", &past, false},
		{"PausedUntil exactly now", &now, false},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			u := &User{ID: 1, TelegramID: 42, PausedUntil: tc.pausedUntil}
			got := u.IsPaused(now)
			if got != tc.want {
				t.Fatalf("IsPaused() = %v, want %v", got, tc.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// UserFeedback struct fields (smoke test — pure data, no methods)
// ---------------------------------------------------------------------------

func TestUserFeedback_fields_are_accessible(t *testing.T) {
	fb := UserFeedback{UserID: 7, JobID: 99, Feedback: FeedbackGood}
	if fb.UserID != 7 {
		t.Fatalf("UserID = %d, want 7", fb.UserID)
	}
	if fb.JobID != 99 {
		t.Fatalf("JobID = %d, want 99", fb.JobID)
	}
	if fb.Feedback != FeedbackGood {
		t.Fatalf("Feedback = %q, want %q", fb.Feedback, FeedbackGood)
	}
}

func TestFeedbackType_string_values(t *testing.T) {
	if string(FeedbackGood) != "good" {
		t.Fatalf("FeedbackGood string value = %q, want \"good\"", FeedbackGood)
	}
	if string(FeedbackBad) != "bad" {
		t.Fatalf("FeedbackBad string value = %q, want \"bad\"", FeedbackBad)
	}
}

// ---------------------------------------------------------------------------
// Job struct fields (smoke test)
// ---------------------------------------------------------------------------

func TestJob_fields_are_accessible(t *testing.T) {
	now := time.Now()
	j := Job{
		ID:         1,
		Source:     "kwork",
		URL:        "https://kwork.ru/projects/1",
		ExternalID: "ext-1",
		Title:      "Go developer",
		Budget:     "5000",
		Skills:     []string{"go", "postgres"},
		LastSeenAt: now,
		CreatedAt:  now,
	}
	if j.Source != "kwork" {
		t.Fatalf("Source = %q, want \"kwork\"", j.Source)
	}
	if len(j.Skills) != 2 {
		t.Fatalf("len(Skills) = %d, want 2", len(j.Skills))
	}
}

// ---------------------------------------------------------------------------
// UserPreferences struct fields (smoke test)
// ---------------------------------------------------------------------------

func TestUserPreferences_fields_are_accessible(t *testing.T) {
	minB := 1000.0
	maxB := 5000.0
	prefs := UserPreferences{
		UserID:           42,
		IncludeKeywords:  []string{"go", "python"},
		ExcludeKeywords:  []string{"php"},
		MinBudget:        &minB,
		MaxBudget:        &maxB,
		PreferredSources: []string{"kwork", "fl.ru"},
	}
	if prefs.UserID != 42 {
		t.Fatalf("UserID = %d, want 42", prefs.UserID)
	}
	if *prefs.MinBudget != 1000.0 {
		t.Fatalf("MinBudget = %f, want 1000.0", *prefs.MinBudget)
	}
	if *prefs.MaxBudget != 5000.0 {
		t.Fatalf("MaxBudget = %f, want 5000.0", *prefs.MaxBudget)
	}
}
