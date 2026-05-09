//go:build integration
// +build integration

package postgres

import (
	"context"
	"errors"
	"testing"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

func TestFeedbackRepository_Upsert_RejectsMissingNotificationHistory(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewFeedbackRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "FeedbackRejectsMissingNotificationHistory")

	err := repo.Upsert(ctx, userID, jobID, domain.FeedbackBad)
	if !errors.Is(err, port.ErrFeedbackNotAllowed) {
		t.Fatalf("Upsert error = %v, want %v", err, port.ErrFeedbackNotAllowed)
	}

	var count int
	if err := pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM user_feedback WHERE user_id = $1 AND job_id = $2
	`, userID, jobID).Scan(&count); err != nil {
		t.Fatalf("count user_feedback: %v", err)
	}
	if count != 0 {
		t.Fatalf("user_feedback count = %d, want 0", count)
	}
}

func TestFeedbackRepository_Upsert_RejectsPendingNotification(t *testing.T) {
	pool := setupTestDBForNotification(t)
	feedbackRepo := NewFeedbackRepository(pool)
	notificationRepo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "FeedbackRejectsPendingNotification")

	if _, _, err := notificationRepo.EnsurePending(ctx, userID, jobID, 0.9, 0.9, "v2", []string{"match"}, ""); err != nil {
		t.Fatalf("EnsurePending: %v", err)
	}

	err := feedbackRepo.Upsert(ctx, userID, jobID, domain.FeedbackBad)
	if !errors.Is(err, port.ErrFeedbackNotAllowed) {
		t.Fatalf("Upsert error = %v, want %v", err, port.ErrFeedbackNotAllowed)
	}
}

func TestFeedbackRepository_Upsert_AllowsFinalizedNotification(t *testing.T) {
	pool := setupTestDBForNotification(t)
	feedbackRepo := NewFeedbackRepository(pool)
	notificationRepo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "FeedbackAllowsFinalizedNotification")

	if _, _, err := notificationRepo.EnsurePending(ctx, userID, jobID, 0.9, 0.9, "v2", []string{"match"}, ""); err != nil {
		t.Fatalf("EnsurePending: %v", err)
	}
	if err := notificationRepo.MarkSent(ctx, userID, jobID); err != nil {
		t.Fatalf("MarkSent: %v", err)
	}

	if err := feedbackRepo.Upsert(ctx, userID, jobID, domain.FeedbackGood); err != nil {
		t.Fatalf("Upsert: %v", err)
	}

	var feedback string
	if err := pool.QueryRow(ctx, `
		SELECT feedback FROM user_feedback WHERE user_id = $1 AND job_id = $2
	`, userID, jobID).Scan(&feedback); err != nil {
		t.Fatalf("query user_feedback: %v", err)
	}
	if feedback != string(domain.FeedbackGood) {
		t.Fatalf("feedback = %q, want %q", feedback, domain.FeedbackGood)
	}
}
