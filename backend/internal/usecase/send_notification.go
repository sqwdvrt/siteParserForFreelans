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

type batchDeliveryItem struct {
	jobID       int64
	wasInserted bool
	payload     port.BatchNotifyItem
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

// ExecuteBatch обрабатывает batch кандидатов:
// EnsurePending для каждого job → rate/daily limit для новых → Send batch → MarkSent по отправленным job.
// Retry (wasInserted=false) сохраняет семантику single-path: не подпадает под rate/daily limit.
func (u *SendNotification) ExecuteBatch(
	ctx context.Context,
	userID int64,
	jobs []port.BatchJobItem,
	criticScore float64,
) error {
	user, err := u.userRepo.GetByID(ctx, userID)
	if err != nil {
		return fmt.Errorf("get user by id: %w", err)
	}
	if user == nil {
		slog.Debug("send batch notification: user not found", "user_id", userID)
		return nil
	}

	seen := make(map[int64]struct{}, len(jobs))
	prepared := make([]batchDeliveryItem, 0, len(jobs))
	for _, item := range jobs {
		if item.JobID <= 0 {
			continue
		}
		if _, exists := seen[item.JobID]; exists {
			continue
		}
		seen[item.JobID] = struct{}{}

		job, err := u.jobRepo.GetByID(ctx, item.JobID)
		if err != nil {
			return fmt.Errorf("get job by id: %w", err)
		}
		if job == nil {
			continue
		}

		// For batch payload we don't have per-item match_score; store neutral score.
		wasInserted, shouldSend, err := u.notifRepo.EnsurePending(ctx, userID, item.JobID, 0)
		if err != nil {
			return err
		}
		if !shouldSend {
			continue
		}

		jobCopy := *job
		if item.Title != "" {
			jobCopy.Title = item.Title
		}
		prepared = append(prepared, batchDeliveryItem{
			jobID:       item.JobID,
			wasInserted: wasInserted,
			payload: port.BatchNotifyItem{
				Job:       &jobCopy,
				WhyItFits: item.WhyItFits,
				Rank:      item.Rank,
			},
		})
	}

	if len(prepared) == 0 {
		slog.Debug("send batch notification: no jobs to send", "user_id", userID)
		return nil
	}

	deliverable, err := u.applyBatchLimits(ctx, userID, prepared)
	if err != nil {
		return err
	}
	if len(deliverable) == 0 {
		return nil
	}

	payload := port.NotifyPayload{
		Batch:       make([]port.BatchNotifyItem, 0, len(deliverable)),
		CriticScore: criticScore,
	}
	for _, item := range deliverable {
		payload.Batch = append(payload.Batch, item.payload)
	}

	if err := u.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		// Pending-записи остаются для retry через Redis Nack.
		slog.Error("send batch notification: telegram failed, pending records kept for retry",
			"user_id", userID, "jobs", len(deliverable), "err", err)
		return err
	}

	for _, item := range deliverable {
		if err := u.notifRepo.MarkSent(ctx, userID, item.jobID); err != nil {
			slog.Error("send batch notification: mark sent failed", "user_id", userID, "job_id", item.jobID, "err", err)
		}
	}
	slog.Info("batch notification sent", "user_id", userID, "jobs", len(deliverable))
	return nil
}

func (u *SendNotification) applyBatchLimits(
	ctx context.Context,
	userID int64,
	items []batchDeliveryItem,
) ([]batchDeliveryItem, error) {
	insertedCount := 0
	for _, item := range items {
		if item.wasInserted {
			insertedCount++
		}
	}
	// Retry-only batch: preserve single-item semantics (skip rate/daily limit).
	if insertedCount == 0 {
		return items, nil
	}

	recent, err := u.notifRepo.SentRecently(ctx, userID, u.rateLimit)
	if err != nil {
		return nil, err
	}
	if recent {
		slog.Debug("send batch notification: rate limited for newly inserted jobs", "user_id", userID)
		return u.keepInsertedJobsAndDeleteRest(ctx, userID, items, 0), nil
	}

	count, err := u.notifRepo.CountToday(ctx, userID)
	if err != nil {
		return nil, err
	}
	remaining := u.maxPerDay - count
	if remaining <= 0 {
		slog.Debug("send batch notification: daily limit reached for newly inserted jobs", "user_id", userID, "count", count)
		return u.keepInsertedJobsAndDeleteRest(ctx, userID, items, 0), nil
	}
	if insertedCount <= remaining {
		return items, nil
	}

	slog.Debug("send batch notification: trimming newly inserted jobs by daily limit",
		"user_id", userID, "count_today", count, "max_per_day", u.maxPerDay, "allowed_new", remaining)
	return u.keepInsertedJobsAndDeleteRest(ctx, userID, items, remaining), nil
}

func (u *SendNotification) keepInsertedJobsAndDeleteRest(
	ctx context.Context,
	userID int64,
	items []batchDeliveryItem,
	keepInserted int,
) []batchDeliveryItem {
	if keepInserted < 0 {
		keepInserted = 0
	}

	out := make([]batchDeliveryItem, 0, len(items))
	insertedKept := 0
	for _, item := range items {
		if !item.wasInserted {
			out = append(out, item)
			continue
		}
		if insertedKept < keepInserted {
			insertedKept++
			out = append(out, item)
			continue
		}
		if err := u.notifRepo.Delete(ctx, userID, item.jobID); err != nil {
			slog.Error("send batch notification: delete on limit failed",
				"user_id", userID, "job_id", item.jobID, "err", err)
		}
	}
	return out
}
