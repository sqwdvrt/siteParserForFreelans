package usecase

import (
	"context"
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

// Execute обрабатывает кандидата: EnsurePending → rate limit (только для новых) → Send → MarkSent.
// Пропускает при: уже доставлено, rate limit, daily limit, отсутствие user/job.
func (u *SendNotification) Execute(ctx context.Context, userID, jobID int64, matchScore float64, whyItFits string) error {
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

	wasInserted, shouldSend, err := u.notifRepo.EnsurePending(ctx, userID, jobID, matchScore)
	if err != nil {
		return err
	}
	if !shouldSend {
		slog.Debug("send notification: already sent, skip", "user_id", userID, "job_id", jobID)
		return nil
	}

	// Rate limit и daily limit применяются только к новым уведомлениям, не к retry.
	if wasInserted {
		recent, err := u.notifRepo.SentRecently(ctx, userID, u.rateLimit)
		if err != nil {
			return err
		}
		if recent {
			slog.Debug("send notification: rate limited", "user_id", userID)
			if delErr := u.notifRepo.Delete(ctx, userID, jobID); delErr != nil {
				slog.Error("send notification: delete on rate limit failed", "user_id", userID, "err", delErr)
			}
			return nil
		}

		count, err := u.notifRepo.CountToday(ctx, userID)
		if err != nil {
			return err
		}
		if count >= u.maxPerDay {
			slog.Debug("send notification: daily limit reached", "user_id", userID, "count", count)
			if delErr := u.notifRepo.Delete(ctx, userID, jobID); delErr != nil {
				slog.Error("send notification: delete on daily limit failed", "user_id", userID, "err", delErr)
			}
			return nil
		}
	}

	payload := port.NotifyPayload{
		Job:       job,
		Score:     matchScore,
		WhyItFits: whyItFits,
	}
	if err := u.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		// Запись остаётся 'pending' — Redis-очередь повторит через Nack.
		slog.Error("send notification: telegram failed, pending record kept for retry",
			"user_id", userID, "job_id", jobID, "err", err)
		return err
	}

	if err := u.notifRepo.MarkSent(ctx, userID, jobID); err != nil {
		slog.Error("send notification: mark sent failed", "user_id", userID, "job_id", jobID, "err", err)
		// Уведомление доставлено — не возвращаем ошибку, только логируем
	}
	slog.Info("notification sent", "user_id", userID, "job_id", jobID)
	return nil
}
