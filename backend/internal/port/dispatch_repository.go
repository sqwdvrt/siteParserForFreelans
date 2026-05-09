package port

import (
	"context"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// QueueDispatchTrace stores correlation metadata for deferred queue delivery.
type QueueDispatchTrace struct {
	TraceID     string
	Traceparent string
}

type PendingUserEmbed struct {
	UserID int64
	Trace  QueueDispatchTrace
}

type PendingJobEmbed struct {
	JobID int64
	Trace QueueDispatchTrace
}

// UserEmbedDispatchRepository atomically persists profile updates and deferred user-embed dispatch.
type UserEmbedDispatchRepository interface {
	UpdateProfileScopedAndStage(ctx context.Context, userID int64, profileText string, trace QueueDispatchTrace) error
	ClaimPendingUserEmbeds(ctx context.Context, limit int, lease time.Duration) ([]PendingUserEmbed, error)
	DeletePendingUserEmbeds(ctx context.Context, userIDs []int64) error
	ReleasePendingUserEmbeds(ctx context.Context, userIDs []int64) error
}

// JobEmbedDispatchRepository atomically persists jobs and deferred ai-process dispatch.
type JobEmbedDispatchRepository interface {
	SaveAndStageJobForEmbedding(ctx context.Context, job *domain.Job, trace QueueDispatchTrace) (id int64, inserted bool, err error)
	StageJobForEmbedding(ctx context.Context, jobID int64, trace QueueDispatchTrace) error
	ClaimPendingJobEmbeds(ctx context.Context, limit int, lease time.Duration) ([]PendingJobEmbed, error)
	DeletePendingJobEmbeds(ctx context.Context, jobIDs []int64) error
	ReleasePendingJobEmbeds(ctx context.Context, jobIDs []int64) error
}
