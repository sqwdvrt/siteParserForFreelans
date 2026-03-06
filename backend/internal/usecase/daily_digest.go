package usecase

import (
	"context"
	"log/slog"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const moscowLocation = "Europe/Moscow"

// DailyDigest отправляет pro-пользователям накопленные pending-уведомления в заданный час.
type DailyDigest struct {
	userRepo  port.UserRepository
	notifRepo port.NotificationRepository
	jobRepo   port.JobRepository
	notifier  port.Notifier
	maxPerDay int
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

// Execute запускает дайджест для pro-пользователей с notify_hour = текущий московский час.
func (d *DailyDigest) Execute(ctx context.Context) {
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

	countToday, err := d.notifRepo.CountToday(ctx, userID)
	if err != nil {
		return err
	}
	remaining := d.maxPerDay - countToday
	if remaining <= 0 {
		slog.Debug("daily digest: daily limit already reached", "user_id", userID)
		return nil
	}

	pending, err := d.notifRepo.GetPendingForUser(ctx, userID)
	if err != nil {
		return err
	}
	if len(pending) == 0 {
		slog.Debug("daily digest: no pending notifications", "user_id", userID)
		return nil
	}

	// Ограничиваем по remaining.
	if len(pending) > remaining {
		pending = pending[:remaining]
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
			continue
		}
		items = append(items, port.BatchNotifyItem{
			Job:       job,
			WhyItFits: p.WhyItFits,
			Rank:      i + 1,
			FinalScore: p.MatchScore,
		})
		delivered = append(delivered, p.JobID)
	}
	if len(items) == 0 {
		return nil
	}

	payload := port.NotifyPayload{Batch: items}
	if err := d.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		slog.Error("daily digest: telegram send failed", "user_id", userID, "jobs", len(items), "err", err)
		return err
	}

	for _, jobID := range delivered {
		if err := d.notifRepo.MarkSent(ctx, userID, jobID); err != nil {
			slog.Error("daily digest: mark sent failed", "user_id", userID, "job_id", jobID, "err", err)
		}
	}
	slog.Info("daily digest: sent", "user_id", userID, "jobs", len(delivered))
	return nil
}
