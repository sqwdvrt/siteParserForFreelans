package postgres

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// setupTestDB подключается к Postgres из DATABASE_URL.
// Запуск: docker compose up -d && export DATABASE_URL=... && go test ./internal/adapter/postgres/ -v
func setupTestDB(t *testing.T) *pgxpool.Pool {
	t.Helper()
	if os.Getenv("INTEGRATION_TESTS") != "1" {
		t.Skip("INTEGRATION_TESTS!=1, skip integration tests")
	}
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

func TestJobRepository_Save(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	job := &domain.Job{
		Source:      "kwork",
		URL:         "https://kwork.ru/projects/123/view",
		ExternalID:  "123",
		Title:       "Test Job",
		Description: "Test description",
		Budget:      "5000 руб",
		Skills:      []string{"Go", "PostgreSQL"},
		RawHTML:     "<html>test</html>",
		CreatedAt:   time.Now(),
	}
	id, err := repo.Save(ctx, job)
	if err != nil {
		t.Fatalf("Save: %v", err)
	}
	if id <= 0 {
		t.Errorf("want positive id, got %d", id)
	}
}

func TestJobRepository_Save_Dedup(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	job := &domain.Job{
		Source:    "kwork",
		URL:       "https://kwork.ru/projects/456/view",
		Title:     "Dup Job",
		RawHTML:   "<html>v1</html>",
		CreatedAt: time.Now(),
	}
	id1, err := repo.Save(ctx, job)
	if err != nil {
		t.Fatalf("Save 1: %v", err)
	}
	job.RawHTML = "<html>v2</html>"
	id2, err := repo.Save(ctx, job)
	if err != nil {
		t.Fatalf("Save 2: %v", err)
	}
	if id1 != id2 {
		t.Errorf("dedup: want same id %d, got %d", id1, id2)
	}
}

func TestJobRepository_ExistsByURL(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	// Уникальный URL, чтобы не конфликтовать с данными от предыдущих запусков
	uniqueURL := "https://kwork.ru/projects/exists-test-" + time.Now().Format("20060102150405") + "/view"
	exists, err := repo.ExistsByURL(ctx, uniqueURL)
	if err != nil {
		t.Fatalf("ExistsByURL: %v", err)
	}
	if exists {
		t.Error("want false for new URL")
	}

	job := &domain.Job{
		Source:    "kwork",
		URL:       uniqueURL,
		Title:     "Exists Test",
		RawHTML:   "<html></html>",
		CreatedAt: time.Now(),
	}
	_, err = repo.Save(ctx, job)
	if err != nil {
		t.Fatalf("Save: %v", err)
	}
	exists, err = repo.ExistsByURL(ctx, job.URL)
	if err != nil {
		t.Fatalf("ExistsByURL 2: %v", err)
	}
	if !exists {
		t.Error("want true after Save")
	}
}

func TestJobRepository_GetByID_NotFound(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	job, err := repo.GetByID(ctx, 9876543210)
	if err != nil {
		t.Fatalf("GetByID: unexpected error for non-existent job: %v", err)
	}
	if job != nil {
		t.Fatalf("GetByID: want nil for non-existent job, got %+v", job)
	}
}

func TestJobRepository_GetByID_Found(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	job := &domain.Job{
		Source:    "kwork",
		URL:       "https://kwork.ru/projects/getbyid-" + time.Now().Format("20060102150405") + "/view",
		Title:     "GetByID Test",
		RawHTML:   "<html>found</html>",
		CreatedAt: time.Now(),
	}
	id, err := repo.Save(ctx, job)
	if err != nil {
		t.Fatalf("Save: %v", err)
	}

	got, err := repo.GetByID(ctx, id)
	if err != nil {
		t.Fatalf("GetByID: %v", err)
	}
	if got == nil {
		t.Fatal("GetByID: want job, got nil")
	}
	if got.ID != id || got.Title != "GetByID Test" || got.RawHTML != "<html>found</html>" {
		t.Errorf("GetByID: got %+v", got)
	}
}
