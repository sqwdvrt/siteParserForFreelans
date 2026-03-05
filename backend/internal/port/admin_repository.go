package port

import (
	"context"
	"time"
)

// AdminStats — агрегированная статистика системы.
type AdminStats struct {
	TotalUsers             int64 `json:"total_users"`
	UsersWithProfile       int64 `json:"users_with_profile"`
	UsersWithEmbedding     int64 `json:"users_with_embedding"`
	TotalJobs              int64 `json:"total_jobs"`
	JobsLast7Days          int64 `json:"jobs_last_7_days"`
	TotalJobEmbeddings     int64 `json:"total_job_embeddings"`
	TotalNotificationsSent int64 `json:"total_notifications_sent"`
	NotificationsToday     int64 `json:"notifications_today"`
	PendingNotifications   int64 `json:"pending_notifications"`
	PendingACJobs          int64 `json:"pending_ac_jobs"`
}

// AdminUser — пользователь для панели администратора.
type AdminUser struct {
	ID                int64      `json:"id"`
	TelegramID        int64      `json:"telegram_id"`
	HasProfile        bool       `json:"has_profile"`
	HasEmbedding      bool       `json:"has_embedding"`
	CreatedAt         time.Time  `json:"created_at"`
	UpdatedAt         time.Time  `json:"updated_at"`
	NotificationsSent int64      `json:"notifications_sent"`
	ProfileSnippet    string     `json:"profile_snippet,omitempty"` // первые 200 символов
}

// AdminJob — задача для панели администратора.
type AdminJob struct {
	ID        int64      `json:"id"`
	Source    string     `json:"source"`
	Title     string     `json:"title"`
	URL       string     `json:"url"`
	Budget    string     `json:"budget,omitempty"`
	HasEmbed  bool       `json:"has_embedding"`
	PostedAt  *time.Time `json:"posted_at,omitempty"`
	CreatedAt time.Time  `json:"created_at"`
}

// AdminRepository — порт для admin-операций (read-heavy, не используется в горячем пути).
type AdminRepository interface {
	GetStats(ctx context.Context) (*AdminStats, error)
	ListUsers(ctx context.Context, limit, offset int) ([]AdminUser, int64, error)
	GetUser(ctx context.Context, userID int64) (*AdminUser, error)
	DeleteUser(ctx context.Context, userID int64) (bool, error) // bool=найден
	ListJobs(ctx context.Context, source string, limit, offset int) ([]AdminJob, int64, error)
}
