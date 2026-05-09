//go:build integration_tc
// +build integration_tc

package postgres

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/modules/postgres"
)

// setupTestDBWithContainers поднимает Postgres в Docker (требует запущенный Docker).
// Запуск: go test -tags=integration_tc ./internal/adapter/postgres/ -v -timeout 120s
func setupTestDBWithContainers(t *testing.T) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()
	migrationsDir, err := filepath.Abs("../../../migrations")
	if err != nil {
		t.Fatalf("abs path: %v", err)
	}
	manifestPath := filepath.Join(migrationsDir, "manifest.txt")
	manifestRaw, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	initScripts := make([]string, 0, 16)
	for _, rawLine := range strings.Split(string(manifestRaw), "\n") {
		entry := strings.TrimSpace(rawLine)
		if entry == "" || strings.HasPrefix(entry, "#") {
			continue
		}
		initScripts = append(initScripts, filepath.Join(migrationsDir, entry))
	}
	if len(initScripts) == 0 {
		t.Fatal("manifest has no migrations")
	}
	ctr, err := postgres.Run(ctx,
		"pgvector/pgvector:pg16",
		postgres.WithDatabase("test"),
		postgres.WithUsername("test"),
		postgres.WithPassword("test"),
		postgres.WithInitScripts(initScripts...),
		postgres.BasicWaitStrategies(),
	)
	if err != nil {
		t.Fatalf("start container: %v", err)
	}
	testcontainers.CleanupContainer(t, ctr)
	connStr, err := ctr.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatalf("connection string: %v", err)
	}
	pool, err := pgxpool.New(ctx, connStr)
	if err != nil {
		t.Fatalf("pgxpool: %v", err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func TestJobRepository_Save_Integration(t *testing.T) {
	pool := setupTestDBWithContainers(t)
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

func TestJobRepository_Save_Dedup_Integration(t *testing.T) {
	pool := setupTestDBWithContainers(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	job := &domain.Job{
		Source:     "kwork",
		URL:        "https://kwork.ru/projects/456/view",
		Title:      "Dup Job",
		RawHTML:    "<html>v1</html>",
		CreatedAt:  time.Now(),
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
