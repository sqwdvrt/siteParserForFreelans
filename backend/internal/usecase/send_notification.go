package usecase

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type batchItemMeta struct {
	job           *domain.Job
	whyItFits     string
	finalScore    float64
	rankerVersion string
	reasonCodes   []string
}

// SendNotification проверяет валидность пары (user, job) и управляет доставкой
// через notifications dedup state; batch-path при наличии notifier отправляет сразу.
type SendNotification struct {
	notifRepo port.NotificationRepository
	userRepo  port.UserRepository
	jobRepo   port.JobRepository
	notifier  port.Notifier
	eventRepo port.ProductEventRepository
	maxPerDay int
}

// NewSendNotification создаёт usecase.
func NewSendNotification(
	notifRepo port.NotificationRepository,
	userRepo port.UserRepository,
	jobRepo port.JobRepository,
) *SendNotification {
	return &SendNotification{
		notifRepo: notifRepo,
		userRepo:  userRepo,
		jobRepo:   jobRepo,
		maxPerDay: defaultMaxPerDay,
	}
}

func (u *SendNotification) WithNotifier(notifier port.Notifier) *SendNotification {
	u.notifier = notifier
	return u
}

func (u *SendNotification) WithProductEventRepo(repo port.ProductEventRepository) *SendNotification {
	u.eventRepo = repo
	return u
}

func (u *SendNotification) WithMaxPerDay(maxPerDay int) *SendNotification {
	if maxPerDay > 0 {
		u.maxPerDay = maxPerDay
	}
	return u
}

// Execute обрабатывает одиночного кандидата: проверяет пользователя и задание,
// вызывает EnsurePending для дедупликации, затем откладывает доставку cron-дайджесту.
func (u *SendNotification) Execute(
	ctx context.Context,
	userID, jobID int64,
	matchScore float64,
	finalScore float64,
	rankerVersion string,
	reasonCodes []string,
	whyItFits string,
) error {
	user, err := u.userRepo.GetByID(ctx, userID)
	if err != nil {
		return fmt.Errorf("get user by id: %w", err)
	}
	if user == nil {
		slog.Debug("send notification: user not found", "user_id", userID)
		return nil
	}
	if user.IsPaused(time.Now()) {
		slog.Debug("send notification: paused user skipped", "user_id", userID)
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

	effectiveFinalScore := finalScore
	if effectiveFinalScore <= 0 {
		effectiveFinalScore = matchScore
	}
	_, shouldSend, err := u.notifRepo.EnsurePending(
		ctx,
		userID,
		jobID,
		matchScore,
		effectiveFinalScore,
		rankerVersion,
		reasonCodes,
		whyItFits,
	)
	if err != nil {
		return err
	}
	if !shouldSend {
		slog.Debug("send notification: already sent, skip", "user_id", userID, "job_id", jobID)
		return nil
	}
	if !notificationJobIsFresh(job, time.Now()) {
		slog.Info("send notification: stale job skipped", "user_id", userID, "job_id", jobID, "source", job.Source)
		if delErr := u.notifRepo.Delete(ctx, userID, jobID); delErr != nil {
			slog.Error("send notification: delete stale notification failed", "user_id", userID, "job_id", jobID, "err", delErr)
		}
		return nil
	}

	slog.Debug("send notification: pending, deferred to accumulation cron", "user_id", userID, "job_id", jobID)
	return nil
}

// ExecuteBatch обрабатывает batch кандидатов: дедупликация через EnsurePending для каждого job,
// проверка свежести и отложенная доставка через суточный digest.
func (u *SendNotification) ExecuteBatch(
	ctx context.Context,
	userID int64,
	jobs []port.BatchJobItem,
	batchScore float64,
	source string,
) error {
	user, err := u.userRepo.GetByID(ctx, userID)
	if err != nil {
		return fmt.Errorf("get user by id: %w", err)
	}
	if user == nil {
		slog.Debug("send batch notification: user not found", "user_id", userID)
		return nil
	}
	if user.IsPaused(time.Now()) {
		slog.Debug("send batch notification: paused user skipped", "user_id", userID)
		return nil
	}

	metaByID := make(map[int64]batchItemMeta, len(jobs))
	uniqueIDs := make([]int64, 0, len(jobs))
	for _, item := range jobs {
		if item.JobID <= 0 {
			continue
		}
		current, dup := metaByID[item.JobID]
		if !dup || item.FinalScore > current.finalScore {
			metaByID[item.JobID] = batchItemMeta{
				whyItFits:     item.WhyItFits,
				finalScore:    item.FinalScore,
				rankerVersion: item.RankerVersion,
				reasonCodes:   item.ReasonCodes,
			}
			if !dup {
				uniqueIDs = append(uniqueIDs, item.JobID)
			}
		}
	}

	jobsMap, err := u.jobRepo.GetByIDs(ctx, uniqueIDs)
	if err != nil {
		return fmt.Errorf("get jobs by ids: %w", err)
	}

	deliverable := make([]batchItemMeta, 0, len(uniqueIDs))
	for _, jobID := range uniqueIDs {
		job, ok := jobsMap[jobID]
		if !ok {
			continue
		}
		meta := metaByID[jobID]
		_, shouldSend, ensureErr := u.notifRepo.EnsurePending(
			ctx,
			userID,
			jobID,
			meta.finalScore,
			meta.finalScore,
			meta.rankerVersion,
			meta.reasonCodes,
			meta.whyItFits,
		)
		if ensureErr != nil {
			return ensureErr
		}
		if !shouldSend {
			continue
		}
		if !notificationJobIsFresh(job, time.Now()) {
			slog.Info("send batch notification: stale job skipped", "user_id", userID, "job_id", jobID, "source", job.Source)
			if delErr := u.notifRepo.Delete(ctx, userID, jobID); delErr != nil {
				slog.Error("send batch notification: delete stale notification failed", "user_id", userID, "job_id", jobID, "err", delErr)
			}
			continue
		}
		meta.job = job
		deliverable = append(deliverable, meta)
	}

	if len(deliverable) == 0 {
		return nil
	}

	// Batch notifications are accumulated in `notifications` and emitted by the
	// digest path so users receive one clean list instead of multiple ad-hoc sends.
	slog.Info(
		"send batch notification: deferred to digest",
		"user_id", userID,
		"source", source,
		"jobs", len(deliverable),
		"batch_score", batchScore,
	)
	return nil
}

func (u *SendNotification) selectBatchWithinDailyCap(
	ctx context.Context,
	userID int64,
	items []batchItemMeta,
) ([]batchItemMeta, error) {
	if len(items) == 0 {
		return nil, nil
	}
	if u.maxPerDay <= 0 {
		return items, nil
	}

	countToday, err := u.notifRepo.CountToday(ctx, userID)
	if err != nil {
		return nil, err
	}
	remaining := u.maxPerDay - countToday
	if remaining <= 0 {
		for _, item := range items {
			if err := u.notifRepo.MarkMissed(ctx, userID, item.job.ID); err != nil {
				return nil, err
			}
		}
		return nil, nil
	}
	if len(items) <= remaining {
		return items, nil
	}

	for _, item := range items[remaining:] {
		if err := u.notifRepo.MarkMissed(ctx, userID, item.job.ID); err != nil {
			return nil, err
		}
	}
	return items[:remaining], nil
}

func (u *SendNotification) recordNotificationSent(
	ctx context.Context,
	userID int64,
	job *domain.Job,
	deliveryMode string,
) error {
	if u.eventRepo == nil || job == nil {
		return nil
	}
	return u.eventRepo.Record(ctx, port.ProductEvent{
		Type:   port.ProductEventNotificationSent,
		UserID: userID,
		JobID:  job.ID,
		Source: job.Source,
		Properties: map[string]any{
			"delivery_mode": deliveryMode,
		},
	})
}
