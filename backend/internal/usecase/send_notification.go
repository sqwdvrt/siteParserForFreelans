package usecase

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const (
	defaultRateLimit = 5 * time.Minute
	defaultMaxPerDay = 5
)

// SendNotification проверяет rate limit, лимит в день и дедупликацию, записывает в notifications, отправляет.
type SendNotification struct {
	notifRepo port.NotificationRepository
	userRepo  port.UserRepository
	jobRepo   port.JobRepository
	notifier  port.Notifier
	rateLimit time.Duration
	maxPerDay int
}

// NewSendNotification создаёт usecase.
func NewSendNotification(
	notifRepo port.NotificationRepository,
	userRepo port.UserRepository,
	jobRepo port.JobRepository,
	notifier port.Notifier,
	rateLimit time.Duration,
	maxPerDay int,
) *SendNotification {
	if rateLimit <= 0 {
		rateLimit = defaultRateLimit
	}
	if maxPerDay <= 0 {
		maxPerDay = defaultMaxPerDay
	}
	return &SendNotification{
		notifRepo: notifRepo,
		userRepo:  userRepo,
		jobRepo:   jobRepo,
		notifier:  notifier,
		rateLimit: rateLimit,
		maxPerDay: maxPerDay,
	}
}

// Execute обрабатывает кандидата: rate limit → запись в notifications → Send.
// Пропускает при: rate limit, дубликат, отсутствие user/job.
func (u *SendNotification) Execute(ctx context.Context, userID, jobID int64, matchScore float64) error {
	user, err := u.userRepo.GetByID(ctx, userID)
	if err != nil {
		return fmt.Errorf("get user by id: %w", err)
	}
	if user == nil {
		slog.Debug("send notification: user not found", "user_id", userID)
		return nil
	}

	job, err := u.jobRepo.GetByID(ctx, jobID)
	if err != nil {
		return fmt.Errorf("get job by id: %w", err)
	}
	if job == nil {
		slog.Debug("send notification: job not found", "job_id", jobID)
		return nil
	}

	recent, err := u.notifRepo.SentRecently(ctx, userID, u.rateLimit)
	if err != nil {
		return err
	}
	if recent {
		slog.Debug("send notification: rate limited", "user_id", userID)
		return nil
	}

	count, err := u.notifRepo.CountToday(ctx, userID)
	if err != nil {
		return err
	}
	if count >= u.maxPerDay {
		slog.Debug("send notification: daily limit reached", "user_id", userID, "count", count)
		return nil
	}

	inserted, err := u.notifRepo.Record(ctx, userID, jobID, matchScore)
	if err != nil {
		return err
	}
	if !inserted {
		slog.Debug("send notification: duplicate skipped", "user_id", userID, "job_id", jobID)
		return nil
	}

	payload := port.NotifyPayload{
		Job:       job,
		Score:     matchScore,
		WhyItFits: "", // заполняется при интеграции с ai_metadata
	}
	if err := u.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		slog.Error("send notification: telegram failed", "user_id", userID, "err", err)
		if rollbackErr := u.notifRepo.Delete(ctx, userID, jobID); rollbackErr != nil {
			slog.Error("send notification: rollback failed", "user_id", userID, "job_id", jobID, "err", rollbackErr)
			return errors.Join(err, fmt.Errorf("rollback notification record: %w", rollbackErr))
		}
		return err
	}
	slog.Info("notification sent", "user_id", userID, "job_id", jobID)
	return nil
}
