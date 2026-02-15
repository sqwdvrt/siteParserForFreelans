package postgres

import (
	"context"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// UserRepository реализует port.UserRepository для PostgreSQL.
type UserRepository struct {
	pool *pgxpool.Pool
}

// NewUserRepository создаёт репозиторий пользователей.
func NewUserRepository(pool *pgxpool.Pool) *UserRepository {
	return &UserRepository{pool: pool}
}

// Save создаёт пользователя по telegram_id. При дубликате возвращает существующий id.
func (r *UserRepository) Save(ctx context.Context, telegramID int64) (int64, error) {
	var id int64
	err := r.pool.QueryRow(ctx, `
		INSERT INTO users (telegram_id)
		VALUES ($1)
		ON CONFLICT (telegram_id) DO UPDATE SET updated_at = NOW()
		RETURNING id
	`, telegramID).Scan(&id)
	return id, err
}

// GetByTelegramID возвращает пользователя по telegram_id. nil если не найден.
func (r *UserRepository) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	var u domain.User
	err := r.pool.QueryRow(ctx, `
		SELECT id, telegram_id, profile_text FROM users WHERE telegram_id = $1
	`, telegramID).Scan(&u.ID, &u.TelegramID, &u.ProfileText)
	if err != nil {
		return nil, err // pgx.ErrNoRows при отсутствии
	}
	return &u, nil
}

// UpdateProfile обновляет profile_text пользователя.
func (r *UserRepository) UpdateProfile(ctx context.Context, userID int64, profileText string) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE users SET profile_text = $1, updated_at = NOW() WHERE id = $2
	`, profileText, userID)
	return err
}
