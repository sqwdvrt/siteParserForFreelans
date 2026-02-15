package port

import "context"

// JobQueue — очередь для передачи job_id в AI Service.
type JobQueue interface {
	Enqueue(ctx context.Context, jobID int64) error
}
