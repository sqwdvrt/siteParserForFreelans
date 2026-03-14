package port

import (
	"context"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// UserRepository — репозиторий пользователей.
type UserRepository interface {
	Save(ctx context.Context, telegramID int64) (userID int64, created bool, err error)
	GetByID(ctx context.Context, userID int64) (*domain.User, error)
	GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error)
	GetPreferencesScoped(ctx context.Context, userID int64) (*domain.UserPreferences, error)
	UpdateProfile(ctx context.Context, userID int64, profileText string) error
	// UpdateProfileScoped выполняет обновление профиля в транзакции с установкой app.current_user_id для RLS.
	UpdateProfileScoped(ctx context.Context, userID int64, profileText string) error
	// UpdateNotifyHourScoped обновляет notify_hour в транзакции с установкой app.current_user_id для RLS.
	UpdateNotifyHourScoped(ctx context.Context, userID int64, hour int) error
	// UpsertPreferencesScoped обновляет user_preferences в транзакции с установкой app.current_user_id для RLS.
	UpsertPreferencesScoped(ctx context.Context, userID int64, prefs domain.UserPreferences) error
	// GetProUsersWithNotifyHour возвращает IDs pro-пользователей с заданным notify_hour (Europe/Moscow).
	GetProUsersWithNotifyHour(ctx context.Context, hour int) ([]int64, error)
}
