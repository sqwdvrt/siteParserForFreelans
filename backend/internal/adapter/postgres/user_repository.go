package postgres

import (
	"context"
	"database/sql"
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
		SELECT id, telegram_id, profile_text, is_pro, notify_hour FROM users WHERE id = $1
	`, userID).Scan(&u.ID, &u.TelegramID, &u.ProfileText, &u.IsPro, &u.NotifyHour)
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
		SELECT id, telegram_id, profile_text, is_pro, notify_hour FROM users WHERE telegram_id = $1
	`, telegramID).Scan(&u.ID, &u.TelegramID, &u.ProfileText, &u.IsPro, &u.NotifyHour)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &u, nil
}

// GetPreferencesScoped читает user_preferences в транзакции с app.current_user_id для RLS.
func (r *UserRepository) GetPreferencesScoped(ctx context.Context, userID int64) (*domain.UserPreferences, error) {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, setUserScopeSQL, strconv.FormatInt(userID, 10)); err != nil {
		return nil, err
	}

	var (
		includeKeywords  []string
		excludeKeywords  []string
		preferredSources []string
		minBudget        sql.NullFloat64
		maxBudget        sql.NullFloat64
		updatedAt        sql.NullTime
	)
	err = tx.QueryRow(ctx, `
		SELECT
			COALESCE(include_keywords, '{}'),
			COALESCE(exclude_keywords, '{}'),
			min_budget::double precision,
			max_budget::double precision,
			COALESCE(preferred_sources, '{}'),
			updated_at
		FROM user_preferences
		WHERE user_id = $1
	`, userID).Scan(
		&includeKeywords,
		&excludeKeywords,
		&minBudget,
		&maxBudget,
		&preferredSources,
		&updatedAt,
	)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}

	prefs := &domain.UserPreferences{
		UserID:           userID,
		IncludeKeywords:  cloneStrings(includeKeywords),
		ExcludeKeywords:  cloneStrings(excludeKeywords),
		PreferredSources: cloneStrings(preferredSources),
	}
	if minBudget.Valid {
		value := minBudget.Float64
		prefs.MinBudget = &value
	}
	if maxBudget.Valid {
		value := maxBudget.Float64
		prefs.MaxBudget = &value
	}
	if updatedAt.Valid {
		prefs.UpdatedAt = updatedAt.Time
	}
	return prefs, nil
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

// UpdateNotifyHourScoped обновляет notify_hour в транзакции с установкой app.current_user_id для RLS.
func (r *UserRepository) UpdateNotifyHourScoped(ctx context.Context, userID int64, hour int) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, setUserScopeSQL, strconv.FormatInt(userID, 10)); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `
		UPDATE users SET notify_hour = $1, updated_at = NOW() WHERE id = $2
	`, hour, userID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// GetProUsersWithNotifyHour возвращает IDs pro-пользователей, у которых notify_hour совпадает
// с текущим часом по московскому времени (Europe/Moscow).
func (r *UserRepository) GetProUsersWithNotifyHour(ctx context.Context, hour int) ([]int64, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id FROM users
		WHERE is_pro = TRUE
		  AND notify_hour = $1
	`, hour)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

// UpsertPreferencesScoped обновляет user_preferences в транзакции с app.current_user_id для RLS.
func (r *UserRepository) UpsertPreferencesScoped(ctx context.Context, userID int64, prefs domain.UserPreferences) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, setUserScopeSQL, strconv.FormatInt(userID, 10)); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO user_preferences (
			user_id,
			include_keywords,
			exclude_keywords,
			min_budget,
			max_budget,
			preferred_sources,
			updated_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, NOW())
		ON CONFLICT (user_id) DO UPDATE SET
			include_keywords = EXCLUDED.include_keywords,
			exclude_keywords = EXCLUDED.exclude_keywords,
			min_budget = EXCLUDED.min_budget,
			max_budget = EXCLUDED.max_budget,
			preferred_sources = EXCLUDED.preferred_sources,
			updated_at = NOW()
	`,
		userID,
		cloneStrings(prefs.IncludeKeywords),
		cloneStrings(prefs.ExcludeKeywords),
		prefs.MinBudget,
		prefs.MaxBudget,
		cloneStrings(prefs.PreferredSources),
	)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func cloneStrings(items []string) []string {
	if len(items) == 0 {
		return []string{}
	}
	out := make([]string, len(items))
	copy(out, items)
	return out
}
