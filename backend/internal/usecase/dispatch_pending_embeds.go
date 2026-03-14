package usecase

import (
	"context"
	"log/slog"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const defaultDispatchLease = 30 * time.Second

type PendingUserEmbedDispatcher struct {
	repo  port.UserEmbedDispatchRepository
	queue port.UserEmbedQueue
	lease time.Duration
}

func NewPendingUserEmbedDispatcher(
	repo port.UserEmbedDispatchRepository,
	queue port.UserEmbedQueue,
	lease time.Duration,
) *PendingUserEmbedDispatcher {
	if lease <= 0 {
		lease = defaultDispatchLease
	}
	return &PendingUserEmbedDispatcher{repo: repo, queue: queue, lease: lease}
}

func (d *PendingUserEmbedDispatcher) Flush(ctx context.Context, limit int) (int, error) {
	if d == nil || d.repo == nil || d.queue == nil || limit <= 0 {
		return 0, nil
	}
	items, err := d.repo.ClaimPendingUserEmbeds(ctx, limit, d.lease)
	if err != nil {
		return 0, err
	}
	sentIDs := make([]int64, 0, len(items))
	failedIDs := make([]int64, 0)
	for _, item := range items {
		msgCtx := observability.ContextWithQueueDispatchTrace(ctx, item.Trace)
		if err := d.queue.Enqueue(msgCtx, item.UserID); err != nil {
			slog.Warn("user-embed dispatch failed", "user_id", item.UserID, "err", err)
			failedIDs = append(failedIDs, item.UserID)
			continue
		}
		sentIDs = append(sentIDs, item.UserID)
	}
	if err := d.repo.DeletePendingUserEmbeds(ctx, sentIDs); err != nil {
		return 0, err
	}
	if err := d.repo.ReleasePendingUserEmbeds(ctx, failedIDs); err != nil {
		return 0, err
	}
	return len(sentIDs), nil
}

type PendingJobEmbedDispatcher struct {
	repo  port.JobEmbedDispatchRepository
	queue port.JobQueue
	lease time.Duration
}

func NewPendingJobEmbedDispatcher(
	repo port.JobEmbedDispatchRepository,
	queue port.JobQueue,
	lease time.Duration,
) *PendingJobEmbedDispatcher {
	if lease <= 0 {
		lease = defaultDispatchLease
	}
	return &PendingJobEmbedDispatcher{repo: repo, queue: queue, lease: lease}
}

func (d *PendingJobEmbedDispatcher) Flush(ctx context.Context, limit int) (int, error) {
	if d == nil || d.repo == nil || d.queue == nil || limit <= 0 {
		return 0, nil
	}
	items, err := d.repo.ClaimPendingJobEmbeds(ctx, limit, d.lease)
	if err != nil {
		return 0, err
	}
	sentIDs := make([]int64, 0, len(items))
	failedIDs := make([]int64, 0)
	for _, item := range items {
		msgCtx := observability.ContextWithQueueDispatchTrace(ctx, item.Trace)
		if err := d.queue.Enqueue(msgCtx, item.JobID); err != nil {
			slog.Warn("job-embed dispatch failed", "job_id", item.JobID, "err", err)
			failedIDs = append(failedIDs, item.JobID)
			continue
		}
		sentIDs = append(sentIDs, item.JobID)
	}
	if err := d.repo.DeletePendingJobEmbeds(ctx, sentIDs); err != nil {
		return 0, err
	}
	if err := d.repo.ReleasePendingJobEmbeds(ctx, failedIDs); err != nil {
		return 0, err
	}
	return len(sentIDs), nil
}
