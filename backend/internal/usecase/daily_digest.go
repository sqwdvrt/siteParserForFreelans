package usecase

import (
	"context"
	"log/slog"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const moscowLocation = "Europe/Moscow"
const defaultDigestClaimTTL = 15 * time.Minute

// DailyDigest отправляет pro-пользователям накопленные pending-уведомления в заданный час.
type DailyDigest struct {
	userRepo         port.UserRepository
	notifRepo        port.NotificationRepository
	jobRepo          port.JobRepository
	notifier         port.Notifier
	productEventRepo port.ProductEventRepository
	maxPerDay        int
}

// NewDailyDigest создаёт use case дайджеста.
func NewDailyDigest(
	userRepo port.UserRepository,
	notifRepo port.NotificationRepository,
	jobRepo port.JobRepository,
	notifier port.Notifier,
	maxPerDay int,
) *DailyDigest {
	if maxPerDay <= 0 {
		maxPerDay = defaultMaxPerDay
	}
	return &DailyDigest{
		userRepo:  userRepo,
		notifRepo: notifRepo,
		jobRepo:   jobRepo,
		notifier:  notifier,
		maxPerDay: maxPerDay,
	}
}

func (d *DailyDigest) WithProductEventRepo(repo port.ProductEventRepository) *DailyDigest {
	d.productEventRepo = repo
	return d
}

// Execute запускает дайджест для pro-пользователей с notify_hour = текущий московский час.
func (d *DailyDigest) Execute(ctx context.Context) {
	reclaimed, err := d.notifRepo.ReclaimStaleDigestClaims(ctx, defaultDigestClaimTTL)
	if err != nil {
		slog.Error("daily digest: reclaim stale claims failed", "err", err)
		return
	}
	if reclaimed > 0 {
		slog.Warn("daily digest: reclaimed stale claims", "count", reclaimed)
	}

	loc, err := time.LoadLocation(moscowLocation)
	if err != nil {
		slog.Error("daily digest: load moscow location", "err", err)
		return
	}
	hour := time.Now().In(loc).Hour()

	userIDs, err := d.userRepo.GetProUsersWithNotifyHour(ctx, hour)
	if err != nil {
		slog.Error("daily digest: get pro users", "hour", hour, "err", err)
		return
	}
	if len(userIDs) == 0 {
		slog.Debug("daily digest: no pro users for hour", "hour", hour)
		return
	}
	slog.Info("daily digest: processing pro users", "hour", hour, "count", len(userIDs))

	for _, userID := range userIDs {
		if err := d.sendDigestForUser(ctx, userID); err != nil {
			slog.Error("daily digest: send for user failed", "user_id", userID, "err", err)
		}
	}
}

func (d *DailyDigest) sendDigestForUser(ctx context.Context, userID int64) error {
	user, err := d.userRepo.GetByID(ctx, userID)
	if err != nil {
		return err
	}
	if user == nil {
		return nil
	}
	if user.IsPaused(time.Now()) {
		slog.Debug("daily digest: paused user skipped", "user_id", userID)
		return nil
	}

	countToday, err := d.notifRepo.CountToday(ctx, userID)
	if err != nil {
		return err
	}
	remaining := d.maxPerDay - countToday
	if remaining <= 0 {
		slog.Debug("daily digest: daily limit already reached", "user_id", userID)
		return nil
	}
	if recoverErr := d.recoverMissedNotifications(ctx, userID, remaining); recoverErr != nil {
		return recoverErr
	}

	pending, err := d.notifRepo.ClaimPendingDigestNotifications(ctx, userID, remaining)
	if err != nil {
		return err
	}
	if len(pending) == 0 {
		slog.Debug("daily digest: no pending notifications", "user_id", userID)
		return nil
	}

	// Загружаем jobs одним запросом.
	jobIDs := make([]int64, 0, len(pending))
	for _, p := range pending {
		jobIDs = append(jobIDs, p.JobID)
	}
	jobsMap, err := d.jobRepo.GetByIDs(ctx, jobIDs)
	if err != nil {
		return err
	}

	// Собираем batch.
	items := make([]port.BatchNotifyItem, 0, len(pending))
	delivered := make([]int64, 0, len(pending))
	for i, p := range pending {
		job, ok := jobsMap[p.JobID]
		if !ok {
			if err := d.notifRepo.Delete(ctx, userID, p.JobID); err != nil {
				slog.Warn("daily digest: delete missing job notification failed", "user_id", userID, "job_id", p.JobID, "err", err)
			}
			continue
		}
		if !notificationJobIsFresh(job, time.Now()) {
			if err := d.notifRepo.Delete(ctx, userID, p.JobID); err != nil {
				slog.Warn("daily digest: delete stale job notification failed", "user_id", userID, "job_id", p.JobID, "err", err)
			}
			continue
		}
		items = append(items, port.BatchNotifyItem{
			Job:        job,
			WhyItFits:  p.WhyItFits,
			Rank:       i + 1,
			FinalScore: p.MatchScore,
		})
		delivered = append(delivered, p.JobID)
	}
	if len(items) == 0 {
		return nil
	}

	payload := port.NotifyPayload{Batch: items}
	for _, jobID := range delivered {
		if err := d.notifRepo.MarkDispatched(ctx, userID, jobID); err != nil {
			slog.Error("daily digest: mark dispatched failed", "user_id", userID, "job_id", jobID, "err", err)
			return err
		}
	}
	if err := d.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		slog.Error("daily digest: telegram send failed after dispatch finalization", "user_id", userID, "jobs", len(items), "err", err)
		return err
	}

	for _, jobID := range delivered {
		if err := d.notifRepo.MarkSent(ctx, userID, jobID); err != nil {
			slog.Error("daily digest: mark sent failed", "user_id", userID, "job_id", jobID, "err", err)
			continue
		}
		if job, ok := jobsMap[jobID]; ok {
			if err := d.recordNotificationSent(ctx, userID, job, "digest"); err != nil {
				slog.Warn("daily digest: record product event failed", "user_id", userID, "job_id", jobID, "event_type", port.ProductEventNotificationSent, "err", err)
			}
		}
	}
	slog.Info("daily digest: sent", "user_id", userID, "jobs", len(delivered))
	return nil
}

func (d *DailyDigest) recoverMissedNotifications(ctx context.Context, userID int64, remaining int) error {
	if remaining <= 0 {
		return nil
	}
	missed, err := d.notifRepo.GetMissedForUser(ctx, userID)
	if err != nil {
		return err
	}
	if len(missed) == 0 {
		return nil
	}

	convertIDs := make([]int64, 0, remaining)
	deleteIDs := make([]int64, 0)
	for _, item := range missed {
		if item.JobStatus != "" && item.JobStatus != "active" {
			deleteIDs = append(deleteIDs, item.ID)
			continue
		}
		if len(convertIDs) < remaining {
			convertIDs = append(convertIDs, item.ID)
		}
	}
	if len(deleteIDs) > 0 {
		if _, deleteErr := d.notifRepo.DeleteNotifications(ctx, deleteIDs); deleteErr != nil {
			return deleteErr
		}
	}
	if len(convertIDs) == 0 {
		return nil
	}
	_, err = d.notifRepo.ConvertMissedToPending(ctx, convertIDs)
	return err
}

func (d *DailyDigest) recordNotificationSent(ctx context.Context, userID int64, job *domain.Job, deliveryMode string) error {
	if d.productEventRepo == nil || job == nil {
		return nil
	}
	return d.productEventRepo.Record(ctx, port.ProductEvent{
		Type:   port.ProductEventNotificationSent,
		UserID: userID,
		JobID:  job.ID,
		Source: job.Source,
		Properties: map[string]any{
			"delivery_mode": deliveryMode,
		},
	})
}
