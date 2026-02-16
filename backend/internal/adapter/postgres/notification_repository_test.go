package postgres

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func setupTestDBForNotification(t *testing.T) *pgxpool.Pool {
	t.Helper()
	connStr := os.Getenv("DATABASE_URL")
	if connStr == "" {
		t.Skip("DATABASE_URL not set, skip integration tests")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, connStr)
	if err != nil {
		t.Fatalf("pgxpool: %v", err)
	}
	t.Cleanup(pool.Close)
	return pool
}

// createTestUserAndJob создаёт user и job для FK в notifications.
// Уникальность: telegram_id и url включают testName для изоляции тестов.
func createTestUserAndJob(t *testing.T, pool *pgxpool.Pool, testName string) (userID, jobID int64) {
	t.Helper()
	ctx := context.Background()
	// Уникальный telegram_id на тест для изоляции
	telegramID := int64(7777777000 + (os.Getpid() % 100000))
	if len(testName) > 0 {
		for _, c := range testName {
			telegramID = telegramID*31 + int64(c)
		}
		telegramID = telegramID % 10000000000
		if telegramID < 0 {
			telegramID = -telegramID
		}
		telegramID += 7777777000
	}
	var uid, jid int64
	err := pool.QueryRow(ctx, `INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT (telegram_id) DO UPDATE SET updated_at = NOW() RETURNING id`, telegramID).Scan(&uid)
	if err != nil {
		t.Fatalf("create user: %v", err)
	}
	url := fmt.Sprintf("https://kwork.ru/projects/notif-%s-%d/view", testName, time.Now().UnixNano())
	err = pool.QueryRow(ctx, `
		INSERT INTO jobs (source, url, title, raw_html)
		VALUES ('kwork', $1, 'Notif Test', '<p>test</p>')
		ON CONFLICT (url) DO UPDATE SET raw_html = EXCLUDED.raw_html
		RETURNING id
	`, url).Scan(&jid)
	if err != nil {
		t.Fatalf("create job: %v", err)
	}
	return uid, jid
}

func TestNotificationRepository_Record(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "Record")

	inserted, err := repo.Record(ctx, userID, jobID, 0.85)
	if err != nil {
		t.Fatalf("Record: %v", err)
	}
	if !inserted {
		t.Error("want inserted=true on first Record")
	}
}

func TestNotificationRepository_Record_Duplicate(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "Record_Duplicate")

	inserted1, err := repo.Record(ctx, userID, jobID, 0.85)
	if err != nil {
		t.Fatalf("Record 1: %v", err)
	}
	if !inserted1 {
		t.Error("want inserted=true on first Record")
	}

	inserted2, err := repo.Record(ctx, userID, jobID, 0.9)
	if err != nil {
		t.Fatalf("Record 2: %v", err)
	}
	if inserted2 {
		t.Error("want inserted=false on duplicate")
	}
}

func TestNotificationRepository_Delete(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "Delete")

	_, _ = repo.Record(ctx, userID, jobID, 0.85)

	err := repo.Delete(ctx, userID, jobID)
	if err != nil {
		t.Fatalf("Delete: %v", err)
	}

	// Повторный Delete не должен падать
	err = repo.Delete(ctx, userID, jobID)
	if err != nil {
		t.Fatalf("Delete again: %v", err)
	}
}

func TestNotificationRepository_SentRecently(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "SentRecently")

	_, _ = repo.Record(ctx, userID, jobID, 0.85)

	recent, err := repo.SentRecently(ctx, userID, 5*time.Minute)
	if err != nil {
		t.Fatalf("SentRecently: %v", err)
	}
	if !recent {
		t.Error("want true: notification was just recorded with sent_at=NOW()")
	}

	// Другой user — не должно быть recent
	recentOther, err := repo.SentRecently(ctx, userID+99999, 5*time.Minute)
	if err != nil {
		t.Fatalf("SentRecently other: %v", err)
	}
	if recentOther {
		t.Error("want false for other user")
	}
}

func TestNotificationRepository_SentRecently_ZeroDuration(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, _ := createTestUserAndJob(t, pool, "SentRecently_Zero")

	recent, err := repo.SentRecently(ctx, userID, 0)
	if err != nil {
		t.Fatalf("SentRecently: %v", err)
	}
	if recent {
		t.Error("want false for within=0")
	}
}

func TestNotificationRepository_CountToday(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "CountToday")

	n, err := repo.CountToday(ctx, userID)
	if err != nil {
		t.Fatalf("CountToday: %v", err)
	}
	if n != 0 {
		t.Errorf("want 0 before Record, got %d", n)
	}

	_, _ = repo.Record(ctx, userID, jobID, 0.85)

	n, err = repo.CountToday(ctx, userID)
	if err != nil {
		t.Fatalf("CountToday after: %v", err)
	}
	if n != 1 {
		t.Errorf("want 1 after Record, got %d", n)
	}

	// Другой user
	nOther, err := repo.CountToday(ctx, userID+99999)
	if err != nil {
		t.Fatalf("CountToday other: %v", err)
	}
	if nOther != 0 {
		t.Errorf("want 0 for other user, got %d", nOther)
	}
}
