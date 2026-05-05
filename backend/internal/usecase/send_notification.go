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
	notifRepo     port.NotificationRepository
	userRepo      port.UserRepository
	jobRepo       port.JobRepository
	notifier      port.Notifier
	eventRepo     port.ProductEventRepository
	freeMaxPerDay int
	proMaxPerDay  int
}

// NewSendNotification создаёт usecase.
func NewSendNotification(
	notifRepo port.NotificationRepository,
	userRepo port.UserRepository,
	jobRepo port.JobRepository,
) *SendNotification {
	return &SendNotification{
		notifRepo:     notifRepo,
		userRepo:      userRepo,
		jobRepo:       jobRepo,
		freeMaxPerDay: defaultFreeMaxPerDay,
		proMaxPerDay:  defaultProMaxPerDay,
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
		u.proMaxPerDay = maxPerDay
	}
	return u
}

func (u *SendNotification) WithDailyCaps(freeMaxPerDay, proMaxPerDay int) *SendNotification {
	if freeMaxPerDay > 0 {
		u.freeMaxPerDay = freeMaxPerDay
	}
	if proMaxPerDay > 0 {
		u.proMaxPerDay = proMaxPerDay
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
	if user.ID == 0 {
		user.ID = userID
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
// проверка свежести и немедленная batch-отправка, если configured notifier доступен.
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
	if user.ID == 0 {
		user.ID = userID
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

	if u.notifier == nil {
		slog.Debug("send batch notification: pending, deferred to accumulation cron", "user_id", userID, "jobs", len(deliverable))
		return nil
	}

	selected, err := u.selectBatchWithinDailyCap(ctx, user, deliverable)
	if err != nil {
		return err
	}
	if len(selected) == 0 {
		slog.Info("send batch notification: daily cap reached, batch moved to missed", "user_id", userID, "source", source)
		return nil
	}

	payloadItems := make([]port.BatchNotifyItem, 0, len(selected))
	for index, meta := range selected {
		payloadItems = append(payloadItems, port.BatchNotifyItem{
			Job:           meta.job,
			WhyItFits:     meta.whyItFits,
			Rank:          index + 1,
			FinalScore:    meta.finalScore,
			RankerVersion: meta.rankerVersion,
			ReasonCodes:   meta.reasonCodes,
		})
	}

	dispatched := make([]batchItemMeta, 0, len(selected))
	for _, meta := range selected {
		if err := u.notifRepo.MarkDispatched(ctx, userID, meta.job.ID); err != nil {
			for _, dispatchedItem := range dispatched {
				if markErr := u.notifRepo.MarkFailed(ctx, userID, dispatchedItem.job.ID); markErr != nil {
					slog.Error("send batch notification: rollback dispatched status failed", "user_id", userID, "job_id", dispatchedItem.job.ID, "err", markErr)
				}
			}
			return err
		}
		dispatched = append(dispatched, meta)
	}

	payload := port.NotifyPayload{
		Source:     source,
		Batch:      payloadItems,
		BatchScore: batchScore,
	}
	if err := u.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		for _, meta := range selected {
			if markErr := u.notifRepo.MarkFailed(ctx, userID, meta.job.ID); markErr != nil {
				slog.Error("send batch notification: mark failed after telegram error", "user_id", userID, "job_id", meta.job.ID, "err", markErr)
			}
		}
		return err
	}

	for _, meta := range selected {
		if err := u.notifRepo.MarkSent(ctx, userID, meta.job.ID); err != nil {
			slog.Error("send batch notification: mark sent failed", "user_id", userID, "job_id", meta.job.ID, "err", err)
			continue
		}
		dailyCap := u.dailyCapForUser(user)
		if err := u.recordNotificationSent(ctx, userID, meta.job, source, dailyCap, user.EffectiveIsPro(time.Now())); err != nil {
			slog.Warn("send batch notification: record product event failed", "user_id", userID, "job_id", meta.job.ID, "err", err)
		}
	}

	slog.Info("send batch notification: sent", "user_id", userID, "source", source, "jobs", len(selected))
	return nil
}

func (u *SendNotification) selectBatchWithinDailyCap(
	ctx context.Context,
	user *domain.User,
	items []batchItemMeta,
) ([]batchItemMeta, error) {
	if len(items) == 0 {
		return nil, nil
	}
	userID := user.ID
	maxPerDay := u.dailyCapForUser(user)
	if maxPerDay <= 0 {
		return items, nil
	}

	countToday, err := u.notifRepo.CountToday(ctx, userID)
	if err != nil {
		return nil, err
	}
	remaining := maxPerDay - countToday
	if remaining <= 0 {
		for _, item := range items {
			if err := u.notifRepo.MarkMissed(ctx, userID, item.job.ID); err != nil {
				return nil, err
			}
		}
		if err := u.recordCapHit(ctx, user, len(items), maxPerDay); err != nil {
			slog.Warn("send batch notification: record cap hit failed", "user_id", userID, "err", err)
		}
		if err := u.maybeSendCapHitNudge(ctx, user, len(items), maxPerDay); err != nil {
			slog.Warn("send batch notification: send cap hit nudge failed", "user_id", userID, "err", err)
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
	hiddenCount := len(items) - remaining
	if err := u.recordCapHit(ctx, user, hiddenCount, maxPerDay); err != nil {
		slog.Warn("send batch notification: record partial cap hit failed", "user_id", userID, "err", err)
	}
	if err := u.maybeSendCapHitNudge(ctx, user, hiddenCount, maxPerDay); err != nil {
		slog.Warn("send batch notification: send partial cap nudge failed", "user_id", userID, "err", err)
	}
	return items[:remaining], nil
}

func (u *SendNotification) dailyCapForUser(user *domain.User) int {
	if user != nil && user.EffectiveIsPro(time.Now()) {
		return u.proMaxPerDay
	}
	return u.freeMaxPerDay
}

func (u *SendNotification) recordNotificationSent(
	ctx context.Context,
	userID int64,
	job *domain.Job,
	deliveryMode string,
	dailyCap int,
	isPro bool,
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
			"plan":          planName(isPro),
			"daily_cap":     dailyCap,
		},
	})
}

func planName(isPro bool) string {
	if isPro {
		return "pro"
	}
	return "free"
}

func (u *SendNotification) recordCapHit(ctx context.Context, user *domain.User, hiddenCount, dailyCap int) error {
	if u.eventRepo == nil || user == nil || user.ID <= 0 || hiddenCount <= 0 {
		return nil
	}
	return u.eventRepo.Record(ctx, port.ProductEvent{
		Type:   port.ProductEventFreeHiddenByCap,
		UserID: user.ID,
		Properties: map[string]any{
			"hidden_count": hiddenCount,
			"daily_cap":    dailyCap,
			"plan":         deliveryPlanName(user, time.Now()),
		},
	})
}

func (u *SendNotification) maybeSendCapHitNudge(ctx context.Context, user *domain.User, hiddenCount, dailyCap int) error {
	if u.notifier == nil || user == nil || user.TelegramID <= 0 || hiddenCount <= 0 {
		return nil
	}
	now := time.Now().UTC()
	if user.EffectiveIsPro(now) {
		return nil
	}
	if u.eventRepo != nil {
		exists, err := u.eventRepo.ExistsSince(
			ctx,
			user.ID,
			port.ProductEventFreeCapHit,
			startOfUTCDay(now),
			"",
			"",
		)
		if err != nil {
			return err
		}
		if exists {
			return nil
		}
	}
	totalHiddenToday, err := u.notifRepo.CountMissedToday(ctx, user.ID)
	if err != nil {
		return err
	}
	text := formatCapHitMessage(user, totalHiddenToday, dailyCap)
	err = u.notifier.Send(ctx, user.TelegramID, port.NotifyPayload{
		Source: "cap-hit",
		Text:   text,
		InlineKeyboard: [][]port.InlineButton{{
			{Text: "Хочу Pro", CallbackData: "pro:upgrade"},
		}},
	})
	if err != nil {
		return err
	}
	if u.eventRepo != nil {
		return u.eventRepo.Record(ctx, port.ProductEvent{
			Type:   port.ProductEventFreeCapHit,
			UserID: user.ID,
			Properties: map[string]any{
				"hidden_count":     hiddenCount,
				"hidden_today":     totalHiddenToday,
				"daily_cap":        dailyCap,
				"plan":             deliveryPlanName(user, now),
				"upgrade_cta_sent": true,
			},
		})
	}
	return nil
}

func formatCapHitMessage(user *domain.User, hiddenToday, dailyCap int) string {
	hiddenToday = maxInt(hiddenToday, 1)
	if user != nil && user.IsPro && !user.EffectiveIsPro(time.Now()) {
		return fmt.Sprintf(
			"Срок Pro закончился. Сегодня я уже скрыл %d подходящих лидов лимитом Free (%d в день).\n\nВерни Pro, чтобы снова получать до 25 лидов в день, быстрые уведомления и дневную сводку.",
			hiddenToday,
			dailyCap,
		)
	}
	return fmt.Sprintf(
		"Лимит Free на сегодня исчерпан. Я уже скрыл %d подходящих лидов после дневного cap %d.\n\nPro даёт до 25 лидов в день, быстрые уведомления и выбор часа дайджеста.",
		hiddenToday,
		dailyCap,
	)
}

func deliveryPlanName(user *domain.User, now time.Time) string {
	if user == nil {
		return "free"
	}
	if user.EffectiveIsPro(now) {
		return "pro"
	}
	if user.IsPro && user.ProExpiresAt != nil && !user.ProExpiresAt.After(now) {
		return "expired_pro"
	}
	return "free"
}

func startOfUTCDay(now time.Time) time.Time {
	y, m, d := now.UTC().Date()
	return time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
}

func maxInt(value, fallback int) int {
	if value > 0 {
		return value
	}
	return fallback
}
