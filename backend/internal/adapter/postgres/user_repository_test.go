//go:build integration
// +build integration

package postgres

import (
	"context"
	"github.com/jackc/pgx/v5/pgxpool"
	"os"
	"testing"
)

func setupTestDBForUser(t *testing.T) *pgxpool.Pool {
	t.Helper()
	connStr := os.Getenv("DATABASE_URL")
	if connStr == "" {
		t.Fatal("DATABASE_URL not set")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, connStr)
	if err != nil {
		t.Fatalf("pgxpool: %v", err)
	}
	if err := pool.Ping(ctx); err != nil {
		t.Fatalf("postgres ping: %v", err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func TestUserRepository_Save(t *testing.T) {
	pool := setupTestDBForUser(t)
	repo := NewUserRepository(pool)
	ctx := context.Background()

	// Уникальный telegram_id для изоляции
	telegramID := int64(9000000000 + (os.Getpid() % 100000))

	id, err := repo.Save(ctx, telegramID)
	if err != nil {
		t.Fatalf("Save: %v", err)
	}
	if id <= 0 {
		t.Errorf("want positive id, got %d", id)
	}
}

func TestUserRepository_Save_Dedup(t *testing.T) {
	pool := setupTestDBForUser(t)
	repo := NewUserRepository(pool)
	ctx := context.Background()

	telegramID := int64(9000000001 + (os.Getpid() % 100000))

	id1, err := repo.Save(ctx, telegramID)
	if err != nil {
		t.Fatalf("Save 1: %v", err)
	}
	id2, err := repo.Save(ctx, telegramID)
	if err != nil {
		t.Fatalf("Save 2: %v", err)
	}
	if id1 != id2 {
		t.Errorf("dedup: want same id %d, got %d", id1, id2)
	}
}

func TestUserRepository_GetByTelegramID(t *testing.T) {
	pool := setupTestDBForUser(t)
	repo := NewUserRepository(pool)
	ctx := context.Background()

	telegramID := int64(9000000002 + (os.Getpid() % 100000))

	u, err := repo.GetByTelegramID(ctx, telegramID)
	if err != nil {
		t.Fatalf("GetByTelegramID: unexpected error for non-existent user: %v", err)
	}
	if u != nil {
		t.Fatalf("GetByTelegramID: want nil for non-existent user, got %+v", u)
	}

	id, err := repo.Save(ctx, telegramID)
	if err != nil {
		t.Fatalf("Save: %v", err)
	}

	u, err = repo.GetByTelegramID(ctx, telegramID)
	if err != nil {
		t.Fatalf("GetByTelegramID: %v", err)
	}
	if u.ID != id || u.TelegramID != telegramID {
		t.Errorf("got user %+v", u)
	}
}

func TestUserRepository_GetByID_NotFound(t *testing.T) {
	pool := setupTestDBForUser(t)
	repo := NewUserRepository(pool)
	ctx := context.Background()

	u, err := repo.GetByID(ctx, 9876543210)
	if err != nil {
		t.Fatalf("GetByID: unexpected error for non-existent user: %v", err)
	}
	if u != nil {
		t.Fatalf("GetByID: want nil for non-existent user, got %+v", u)
	}
}

func TestUserRepository_UpdateProfile(t *testing.T) {
	pool := setupTestDBForUser(t)
	repo := NewUserRepository(pool)
	ctx := context.Background()

	telegramID := int64(9000000003 + (os.Getpid() % 100000))
	id, err := repo.Save(ctx, telegramID)
	if err != nil {
		t.Fatalf("Save: %v", err)
	}

	err = repo.UpdateProfile(ctx, id, "Python dev, 5 years")
	if err != nil {
		t.Fatalf("UpdateProfile: %v", err)
	}

	u, err := repo.GetByTelegramID(ctx, telegramID)
	if err != nil {
		t.Fatalf("GetByTelegramID: %v", err)
	}
	if u.ProfileText == nil || *u.ProfileText != "Python dev, 5 years" {
		t.Errorf("profile_text not updated: %v", u.ProfileText)
	}
}
