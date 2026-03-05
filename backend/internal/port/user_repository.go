package port

import (
	"context"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// UserRepository — репозиторий пользователей.
type UserRepository interface {
	Save(ctx context.Context, telegramID int64) (int64, error)
	GetByID(ctx context.Context, userID int64) (*domain.User, error)
	GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error)
	UpdateProfile(ctx context.Context, userID int64, profileText string) error
	// UpdateProfileScoped выполняет обновление профиля в транзакции с установкой app.current_user_id для RLS.
	UpdateProfileScoped(ctx context.Context, userID int64, profileText string) error
}
