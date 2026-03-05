package postgres

import (
	"context"
	"errors"
	"strconv"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

const setUserScopeSQL = `SELECT set_config('app.current_user_id', $1, true)`

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

// GetByID возвращает пользователя по id. nil если не найден.
func (r *UserRepository) GetByID(ctx context.Context, userID int64) (*domain.User, error) {
	var u domain.User
	err := r.pool.QueryRow(ctx, `
		SELECT id, telegram_id, profile_text FROM users WHERE id = $1
	`, userID).Scan(&u.ID, &u.TelegramID, &u.ProfileText)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &u, nil
}

// GetByTelegramID возвращает пользователя по telegram_id. nil если не найден.
func (r *UserRepository) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	var u domain.User
	err := r.pool.QueryRow(ctx, `
		SELECT id, telegram_id, profile_text FROM users WHERE telegram_id = $1
	`, telegramID).Scan(&u.ID, &u.TelegramID, &u.ProfileText)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &u, nil
}

// UpdateProfile обновляет profile_text пользователя (без RLS-скопа; для воркеров/миграций).
func (r *UserRepository) UpdateProfile(ctx context.Context, userID int64, profileText string) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE users SET profile_text = $1, updated_at = NOW() WHERE id = $2
	`, profileText, userID)
	return err
}

// UpdateProfileScoped обновляет профиль в транзакции с установкой app.current_user_id для RLS.
// Используется в API, чтобы даже при утечке учётки БД нельзя было менять чужой профиль.
func (r *UserRepository) UpdateProfileScoped(ctx context.Context, userID int64, profileText string) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, setUserScopeSQL, strconv.FormatInt(userID, 10)); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `
		UPDATE users SET profile_text = $1, updated_at = NOW() WHERE id = $2
	`, profileText, userID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
