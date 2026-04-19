package usecase

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// SendNotification проверяет валидность пары (user, job), обеспечивает дедупликацию
// через EnsurePending и возвращает управление — доставка происходит через cron-дайджест.
type SendNotification struct {
	notifRepo port.NotificationRepository
	userRepo  port.UserRepository
	jobRepo   port.JobRepository
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
	}
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
// проверка свежести; доставка происходит через cron-дайджест.
func (u *SendNotification) ExecuteBatch(
	ctx context.Context,
	userID int64,
	jobs []port.BatchJobItem,
	batchScore float64,
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

	type itemMeta struct {
		whyItFits     string
		finalScore    float64
		rankerVersion string
		reasonCodes   []string
	}
	metaByID := make(map[int64]itemMeta, len(jobs))
	uniqueIDs := make([]int64, 0, len(jobs))
	for _, item := range jobs {
		if item.JobID <= 0 {
			continue
		}
		current, dup := metaByID[item.JobID]
		if !dup || item.FinalScore > current.finalScore {
			metaByID[item.JobID] = itemMeta{
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
		}
	}

	slog.Debug("send batch notification: pending, deferred to accumulation cron", "user_id", userID, "jobs", len(uniqueIDs))
	return nil
}
