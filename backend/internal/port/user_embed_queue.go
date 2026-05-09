package port

import "context"

// UserEmbedQueue — очередь для передачи user_id в AI Service (user-embed).
type UserEmbedQueue interface {
	Enqueue(ctx context.Context, userID int64) error
}
