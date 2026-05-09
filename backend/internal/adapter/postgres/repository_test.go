//go:build integration
// +build integration

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
// Запуск: docker compose up -d && export DATABASE_URL=... && go test -tags=integration ./internal/adapter/postgres/ -v
func setupTestDB(t *testing.T) *pgxpool.Pool {
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

	// Expired job не должен считаться существующим — краулер должен
	// повторно скачать и реактивировать его через Save().
	_, err = pool.Exec(ctx, `UPDATE jobs SET status = 'expired' WHERE url = $1`, uniqueURL)
	if err != nil {
		t.Fatalf("expire job: %v", err)
	}
	exists, err = repo.ExistsByURL(ctx, uniqueURL)
	if err != nil {
		t.Fatalf("ExistsByURL after expire: %v", err)
	}
	if exists {
		t.Error("want false for expired job")
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

func TestJobRepository_GetByID_ExpiredFiltered(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	job := &domain.Job{
		Source:    "kwork",
		URL:       "https://kwork.ru/projects/getbyid-expired-" + time.Now().Format("20060102150405") + "/view",
		Title:     "Expired GetByID Test",
		RawHTML:   "<html>expired</html>",
		CreatedAt: time.Now(),
	}
	id, err := repo.Save(ctx, job)
	if err != nil {
		t.Fatalf("Save: %v", err)
	}
	if _, err := pool.Exec(ctx, `UPDATE jobs SET status = 'expired' WHERE id = $1`, id); err != nil {
		t.Fatalf("expire job: %v", err)
	}

	got, err := repo.GetByID(ctx, id)
	if err != nil {
		t.Fatalf("GetByID: %v", err)
	}
	if got != nil {
		t.Fatalf("GetByID: want nil for expired job, got %+v", got)
	}
}

func TestJobRepository_GetByIDs_FiltersExpired(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	activeID, err := repo.Save(ctx, &domain.Job{
		Source:    "kwork",
		URL:       "https://kwork.ru/projects/getbyids-active-" + time.Now().Format("20060102150405") + "/view",
		Title:     "Active GetByIDs Test",
		RawHTML:   "<html>active</html>",
		CreatedAt: time.Now(),
	})
	if err != nil {
		t.Fatalf("Save active: %v", err)
	}
	expiredID, err := repo.Save(ctx, &domain.Job{
		Source:    "kwork",
		URL:       "https://kwork.ru/projects/getbyids-expired-" + time.Now().Add(time.Second).Format("20060102150405") + "/view",
		Title:     "Expired GetByIDs Test",
		RawHTML:   "<html>expired</html>",
		CreatedAt: time.Now(),
	})
	if err != nil {
		t.Fatalf("Save expired: %v", err)
	}
	if _, err := pool.Exec(ctx, `UPDATE jobs SET status = 'expired' WHERE id = $1`, expiredID); err != nil {
		t.Fatalf("expire job: %v", err)
	}

	got, err := repo.GetByIDs(ctx, []int64{activeID, expiredID})
	if err != nil {
		t.Fatalf("GetByIDs: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("GetByIDs len = %d, want 1", len(got))
	}
	if got[activeID] == nil {
		t.Fatalf("GetByIDs: active job %d missing", activeID)
	}
	if _, ok := got[expiredID]; ok {
		t.Fatalf("GetByIDs: expired job %d must be filtered", expiredID)
	}
}

func TestJobRepository_TouchSeenAt_DoesNotReactivateExpiredJob(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	ctx := context.Background()

	url := "https://kwork.ru/projects/no-reactivate-" + time.Now().Format("20060102150405") + "/view"
	job := &domain.Job{
		Source:    "kwork",
		URL:       url,
		Title:     "No-Reactivate Test",
		RawHTML:   "<html>expired</html>",
		CreatedAt: time.Now(),
	}
	if _, err := repo.Save(ctx, job); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if _, err := pool.Exec(ctx, `
		UPDATE jobs
		SET status = 'expired',
		    last_seen_at = NOW() - INTERVAL '20 days'
		WHERE url = $1
	`, url); err != nil {
		t.Fatalf("prepare expired job: %v", err)
	}

	// TouchSeenAt must be a no-op for expired jobs — it must not reactivate them.
	if err := repo.TouchSeenAt(ctx, url); err != nil {
		t.Fatalf("TouchSeenAt: %v", err)
	}

	var status string
	if err := pool.QueryRow(ctx, `SELECT status FROM jobs WHERE url = $1`, url).Scan(&status); err != nil {
		t.Fatalf("QueryRow: %v", err)
	}
	if status != "expired" {
		t.Fatalf("status = %q, want expired (TouchSeenAt must not reactivate expired jobs)", status)
	}
}

func TestJobRepository_ExpireByURL_DeletesPendingNotifications(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewJobRepository(pool)
	notifRepo := NewNotificationRepository(pool)
	ctx := context.Background()

	userTelegramID := int64(8800000000 + time.Now().UnixNano()%1000000)
	var userID int64
	if err := pool.QueryRow(ctx, `
		INSERT INTO users (telegram_id)
		VALUES ($1)
		ON CONFLICT (telegram_id) DO UPDATE SET updated_at = NOW()
		RETURNING id
	`, userTelegramID).Scan(&userID); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	url := "https://kwork.ru/projects/expire-atomic-" + time.Now().Format("20060102150405.000000000") + "/view"
	var jobID int64
	if err := pool.QueryRow(ctx, `
		INSERT INTO jobs (source, url, title, raw_html)
		VALUES ('kwork', $1, 'Expire Atomic Test', '<html>pending</html>')
		ON CONFLICT (url) DO UPDATE SET raw_html = EXCLUDED.raw_html, status = 'active', last_seen_at = NOW()
		RETURNING id
	`, url).Scan(&jobID); err != nil {
		t.Fatalf("insert job: %v", err)
	}
	if _, _, err := notifRepo.EnsurePending(ctx, userID, jobID, 0.9, 0.9, "v2", []string{"close_match"}, "atomic cleanup"); err != nil {
		t.Fatalf("EnsurePending: %v", err)
	}

	expiredIDs, err := repo.ExpireByURL(ctx, url)
	if err != nil {
		t.Fatalf("ExpireByURL: %v", err)
	}
	if len(expiredIDs) != 1 || expiredIDs[0] != jobID {
		t.Fatalf("ExpireByURL ids = %v, want [%d]", expiredIDs, jobID)
	}

	var status string
	if err := pool.QueryRow(ctx, `SELECT status FROM jobs WHERE id = $1`, jobID).Scan(&status); err != nil {
		t.Fatalf("query job status: %v", err)
	}
	if status != "expired" {
		t.Fatalf("job status = %q, want expired", status)
	}

	var pendingCount int
	if err := pool.QueryRow(ctx, `
		SELECT COUNT(*)
		FROM notifications
		WHERE job_id = $1 AND status = 'pending'
	`, jobID).Scan(&pendingCount); err != nil {
		t.Fatalf("query pending notifications: %v", err)
	}
	if pendingCount != 0 {
		t.Fatalf("pending notifications = %d, want 0", pendingCount)
	}
}
