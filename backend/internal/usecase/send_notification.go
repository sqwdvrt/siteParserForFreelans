package usecase

import (
	"context"
	"fmt"
	"log/slog"
	"sort"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
)

const (
	defaultRateLimit = 5 * time.Minute
	defaultMaxPerDay = 5
)

// SendNotification проверяет rate limit, лимит в день и дедупликацию, затем переводит запись в dispatched и отправляет.
type SendNotification struct {
	notifRepo        port.NotificationRepository
	userRepo         port.UserRepository
	jobRepo          port.JobRepository
	notifier         port.Notifier
	productEventRepo port.ProductEventRepository
	e2eMetrics       *telemetry.E2ELatencyMetrics
	rateLimit        time.Duration
	maxPerDay        int
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

func (u *SendNotification) WithProductEventRepo(repo port.ProductEventRepository) *SendNotification {
	u.productEventRepo = repo
	return u
}

func (u *SendNotification) WithE2ELatencyMetrics(metrics *telemetry.E2ELatencyMetrics) *SendNotification {
	u.e2eMetrics = metrics
	return u
}

// Execute обрабатывает кандидата: EnsurePending → rate limit (только для новых) → MarkDispatched → Send → MarkSent.
// Пропускает при: уже доставлено, rate limit, daily limit, отсутствие user/job.
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
	wasInserted, shouldSend, err := u.notifRepo.EnsurePending(
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

	// Pro-пользователи получают уведомления только через дайджест (hourly cron).
	if user.IsPro {
		slog.Debug("send notification: pro user, deferred to digest", "user_id", userID, "job_id", jobID)
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
			if markErr := u.notifRepo.MarkMissed(ctx, userID, jobID); markErr != nil {
				slog.Error("send notification: mark missed on rate limit failed", "user_id", userID, "job_id", jobID, "err", markErr)
			}
			return nil
		}

		count, err := u.notifRepo.CountToday(ctx, userID)
		if err != nil {
			return err
		}
		if count >= u.maxPerDay {
			slog.Debug("send notification: daily limit reached", "user_id", userID, "count", count)
			if markErr := u.notifRepo.MarkMissed(ctx, userID, jobID); markErr != nil {
				slog.Error("send notification: mark missed on daily limit failed", "user_id", userID, "job_id", jobID, "err", markErr)
			}
			return nil
		}
	}

	if err := u.notifRepo.MarkDispatched(ctx, userID, jobID); err != nil {
		slog.Error("send notification: mark dispatched failed", "user_id", userID, "job_id", jobID, "err", err)
		return fmt.Errorf("mark dispatched notification: %w", err)
	}

	payload := port.NotifyPayload{
		Job:           job,
		Score:         effectiveFinalScore,
		FinalScore:    effectiveFinalScore,
		RankerVersion: rankerVersion,
		ReasonCodes:   cloneStrings(reasonCodes),
		WhyItFits:     whyItFits,
	}
	if err := u.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		slog.Error("send notification: telegram failed, notification remains dispatched",
			"user_id", userID, "job_id", jobID, "err", err)
		return nil
	}

	if err := u.notifRepo.MarkSent(ctx, userID, jobID); err != nil {
		slog.Error("send notification: mark sent failed", "user_id", userID, "job_id", jobID, "err", err)
		// Уведомление доставлено — не возвращаем ошибку, только логируем
		return nil
	}
	u.recordNotificationSent(ctx, userID, job, effectiveFinalScore, rankerVersion, "single", reasonCodes)
	u.recordE2ELatency(job)
	slog.Info("notification sent", "user_id", userID, "job_id", jobID)
	return nil
}

// ExecuteBatch обрабатывает batch кандидатов:
// EnsurePending для каждого job → rate/daily limit для новых → MarkDispatched по отправляемым job → Send batch → MarkSent.
// Retry (wasInserted=false) сохраняет семантику single-path: не подпадает под rate/daily limit.
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

	// Дедупликация и сбор уникальных ID для одного batch-запроса.
	type itemMeta struct {
		title         string
		whyItFits     string
		rank          int
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
				title:         item.Title,
				whyItFits:     item.WhyItFits,
				rank:          item.Rank,
				finalScore:    item.FinalScore,
				rankerVersion: item.RankerVersion,
				reasonCodes:   cloneStrings(item.ReasonCodes),
			}
			if !dup {
				uniqueIDs = append(uniqueIDs, item.JobID)
			}
		}
	}

	// Один SQL-запрос вместо N GetByID.
	jobsMap, err := u.jobRepo.GetByIDs(ctx, uniqueIDs)
	if err != nil {
		return fmt.Errorf("get jobs by ids: %w", err)
	}

	prepared := make([]batchDeliveryItem, 0, len(uniqueIDs))
	for _, jobID := range uniqueIDs {
		job, ok := jobsMap[jobID]
		if !ok {
			continue
		}

		meta := metaByID[jobID]
		finalScore := meta.finalScore
		wasInserted, shouldSend, ensureErr := u.notifRepo.EnsurePending(
			ctx,
			userID,
			jobID,
			finalScore,
			finalScore,
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

		jobCopy := *job
		if meta.title != "" {
			jobCopy.Title = meta.title
		}
		prepared = append(prepared, batchDeliveryItem{
			jobID:       jobID,
			wasInserted: wasInserted,
			payload: port.BatchNotifyItem{
				Job:           &jobCopy,
				WhyItFits:     meta.whyItFits,
				Rank:          meta.rank,
				FinalScore:    meta.finalScore,
				RankerVersion: meta.rankerVersion,
				ReasonCodes:   cloneStrings(meta.reasonCodes),
			},
		})
	}

	if len(prepared) == 0 {
		slog.Debug("send batch notification: no jobs to send", "user_id", userID)
		return nil
	}

	// Pro-пользователи получают уведомления только через дайджест (hourly cron).
	if user.IsPro {
		slog.Debug("send batch notification: pro user, deferred to digest", "user_id", userID, "jobs", len(prepared))
		return nil
	}

	deliverable, err := u.applyBatchLimits(ctx, userID, prepared)
	if err != nil {
		return err
	}
	if len(deliverable) == 0 {
		return nil
	}
	sortBatchDeliveryItems(deliverable)
	for idx := range deliverable {
		deliverable[idx].payload.Rank = idx + 1
	}

	for _, item := range deliverable {
		if err := u.notifRepo.MarkDispatched(ctx, userID, item.jobID); err != nil {
			slog.Error("send batch notification: mark dispatched failed", "user_id", userID, "job_id", item.jobID, "err", err)
			return fmt.Errorf("mark dispatched notification: %w", err)
		}
	}

	slog.Info(
		"send batch notification: ranked batch",
		"user_id", userID,
		"jobs", len(deliverable),
		"top_reasons", topReasonCodes(deliverable, 5),
	)

	payload := port.NotifyPayload{
		Batch:      make([]port.BatchNotifyItem, 0, len(deliverable)),
		BatchScore: batchScore,
	}
	for _, item := range deliverable {
		payload.Batch = append(payload.Batch, item.payload)
	}

	if err := u.notifier.Send(ctx, user.TelegramID, payload); err != nil {
		slog.Error("send batch notification: telegram failed, notifications remain dispatched",
			"user_id", userID, "jobs", len(deliverable), "err", err)
		return nil
	}

	for _, item := range deliverable {
		if err := u.notifRepo.MarkSent(ctx, userID, item.jobID); err != nil {
			slog.Error("send batch notification: mark sent failed", "user_id", userID, "job_id", item.jobID, "err", err)
			continue
		}
		if item.payload.Job != nil {
			u.recordNotificationSent(
				ctx,
				userID,
				item.payload.Job,
				item.payload.FinalScore,
				item.payload.RankerVersion,
				"batch",
				item.payload.ReasonCodes,
			)
			u.recordE2ELatency(item.payload.Job)
		}
	}
	slog.Info("batch notification sent", "user_id", userID, "jobs", len(deliverable))
	return nil
}

func sortBatchDeliveryItems(items []batchDeliveryItem) {
	sort.SliceStable(items, func(i, j int) bool {
		left := items[i].payload.FinalScore
		right := items[j].payload.FinalScore
		if left == right {
			ri := items[i].payload.Rank
			rj := items[j].payload.Rank
			if ri <= 0 && rj <= 0 {
				return items[i].jobID < items[j].jobID
			}
			if ri <= 0 {
				return false
			}
			if rj <= 0 {
				return true
			}
			return ri < rj
		}
		return left > right
	})
}

func topReasonCodes(items []batchDeliveryItem, limit int) []string {
	if limit <= 0 {
		return nil
	}
	counts := make(map[string]int, limit)
	for _, item := range items {
		for _, code := range item.payload.ReasonCodes {
			if code == "" {
				continue
			}
			counts[code]++
		}
	}
	if len(counts) == 0 {
		return nil
	}
	type reasonStat struct {
		code  string
		count int
	}
	stats := make([]reasonStat, 0, len(counts))
	for code, count := range counts {
		stats = append(stats, reasonStat{code: code, count: count})
	}
	sort.Slice(stats, func(i, j int) bool {
		if stats[i].count == stats[j].count {
			return stats[i].code < stats[j].code
		}
		return stats[i].count > stats[j].count
	})
	if len(stats) > limit {
		stats = stats[:limit]
	}
	out := make([]string, 0, len(stats))
	for _, stat := range stats {
		out = append(out, stat.code)
	}
	return out
}

func cloneStrings(items []string) []string {
	if len(items) == 0 {
		return nil
	}
	out := make([]string, len(items))
	copy(out, items)
	return out
}

func (u *SendNotification) recordNotificationSent(
	ctx context.Context,
	userID int64,
	job *domain.Job,
	finalScore float64,
	rankerVersion string,
	deliveryMode string,
	reasonCodes []string,
) {
	if u.productEventRepo == nil || job == nil {
		return
	}
	if err := u.productEventRepo.Record(ctx, port.ProductEvent{
		Type:   port.ProductEventNotificationSent,
		UserID: userID,
		JobID:  job.ID,
		Source: job.Source,
		Properties: map[string]any{
			"delivery_mode":  deliveryMode,
			"final_score":    finalScore,
			"ranker_version": rankerVersion,
			"reason_codes":   cloneStrings(reasonCodes),
		},
	}); err != nil {
		slog.Warn("send notification: record product event failed", "user_id", userID, "job_id", job.ID, "event_type", port.ProductEventNotificationSent, "err", err)
	}
}

func (u *SendNotification) recordE2ELatency(job *domain.Job) {
	if u.e2eMetrics == nil || job == nil {
		return
	}

	// E2E latency: от создания job (crawl) до текущего момента (notification sent)
	e2eLatency := time.Since(job.CreatedAt)
	u.e2eMetrics.ObserveE2ELatency(job.Source, e2eLatency)

	// Stage latency: match_to_notify — от создания job до отправки уведомления
	u.e2eMetrics.ObserveStageLatency(telemetry.StageMatchToNotify, job.Source, e2eLatency)
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
		return u.keepInsertedJobsAndMarkMissedRest(ctx, userID, items, 0), nil
	}

	count, err := u.notifRepo.CountToday(ctx, userID)
	if err != nil {
		return nil, err
	}
	remaining := u.maxPerDay - count
	if remaining <= 0 {
		slog.Debug("send batch notification: daily limit reached for newly inserted jobs", "user_id", userID, "count", count)
		return u.keepInsertedJobsAndMarkMissedRest(ctx, userID, items, 0), nil
	}
	if insertedCount <= remaining {
		return items, nil
	}

	slog.Debug("send batch notification: trimming newly inserted jobs by daily limit",
		"user_id", userID, "count_today", count, "max_per_day", u.maxPerDay, "allowed_new", remaining)
	return u.keepInsertedJobsAndMarkMissedRest(ctx, userID, items, remaining), nil
}

func (u *SendNotification) keepInsertedJobsAndMarkMissedRest(
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
		if err := u.notifRepo.MarkMissed(ctx, userID, item.jobID); err != nil {
			slog.Error("send batch notification: mark missed on limit failed",
				"user_id", userID, "job_id", item.jobID, "err", err)
		}
	}
	return out
}
