//go:build integration
// +build integration

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

func TestNotificationRepository_EnsurePending_New(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "EnsurePendingNew")

	wasInserted, shouldSend, err := repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending: %v", err)
	}
	if !wasInserted {
		t.Error("want wasInserted=true on first call")
	}
	if !shouldSend {
		t.Error("want shouldSend=true on first call")
	}
}

func TestNotificationRepository_EnsurePending_RetryWhenPending(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "EnsurePendingRetry")

	// Первый вызов — вставляет pending
	_, _, err := repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending 1: %v", err)
	}

	// Второй вызов при status=pending — wasInserted=false, shouldSend=true
	wasInserted, shouldSend, err := repo.EnsurePending(ctx, userID, jobID, 0.9, 0.9, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending 2: %v", err)
	}
	if wasInserted {
		t.Error("want wasInserted=false on duplicate call")
	}
	if !shouldSend {
		t.Error("want shouldSend=true when status=pending (retry)")
	}
}

func TestNotificationRepository_EnsurePending_SkipWhenSent(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "EnsurePendingSkipSent")

	// Вставляем и помечаем как sent
	_, _, err := repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending: %v", err)
	}
	if err := repo.MarkSent(ctx, userID, jobID); err != nil {
		t.Fatalf("MarkSent: %v", err)
	}

	// После MarkSent — shouldSend=false
	wasInserted, shouldSend, err := repo.EnsurePending(ctx, userID, jobID, 0.9, 0.9, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending after sent: %v", err)
	}
	if wasInserted {
		t.Error("want wasInserted=false when already sent")
	}
	if shouldSend {
		t.Error("want shouldSend=false when status=sent")
	}
}

func TestNotificationRepository_MarkSent(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "MarkSent")

	_, _, err := repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending: %v", err)
	}

	if err := repo.MarkSent(ctx, userID, jobID); err != nil {
		t.Fatalf("MarkSent: %v", err)
	}

	// После MarkSent запись должна считаться sent
	_, shouldSend, err := repo.EnsurePending(ctx, userID, jobID, 0.9, 0.9, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending after MarkSent: %v", err)
	}
	if shouldSend {
		t.Error("want shouldSend=false after MarkSent")
	}
}

func TestNotificationRepository_Delete(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "Delete")

	_, _, _ = repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})

	err := repo.Delete(ctx, userID, jobID)
	if err != nil {
		t.Fatalf("Delete: %v", err)
	}

	// Повторный Delete не должен падать
	err = repo.Delete(ctx, userID, jobID)
	if err != nil {
		t.Fatalf("Delete again: %v", err)
	}

	// После Delete запись должна вставляться снова
	wasInserted, shouldSend, err := repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})
	if err != nil {
		t.Fatalf("EnsurePending after Delete: %v", err)
	}
	if !wasInserted || !shouldSend {
		t.Error("want wasInserted=true, shouldSend=true after Delete")
	}
}

func TestNotificationRepository_SentRecently(t *testing.T) {
	pool := setupTestDBForNotification(t)
	repo := NewNotificationRepository(pool)
	ctx := context.Background()
	userID, jobID := createTestUserAndJob(t, pool, "SentRecently")

	// pending-запись НЕ считается за "sent recently"
	_, _, _ = repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})
	recent, err := repo.SentRecently(ctx, userID, 5*time.Minute)
	if err != nil {
		t.Fatalf("SentRecently (pending): %v", err)
	}
	if recent {
		t.Error("want false for pending record (not yet delivered)")
	}

	// После MarkSent — считается
	if err := repo.MarkSent(ctx, userID, jobID); err != nil {
		t.Fatalf("MarkSent: %v", err)
	}
	recent, err = repo.SentRecently(ctx, userID, 5*time.Minute)
	if err != nil {
		t.Fatalf("SentRecently (sent): %v", err)
	}
	if !recent {
		t.Error("want true: notification was just marked sent")
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
		t.Errorf("want 0 before EnsurePending, got %d", n)
	}

	// pending-запись НЕ считается в CountToday
	_, _, _ = repo.EnsurePending(ctx, userID, jobID, 0.85, 0.85, "v2", []string{"strong_similarity"})
	n, err = repo.CountToday(ctx, userID)
	if err != nil {
		t.Fatalf("CountToday after EnsurePending: %v", err)
	}
	if n != 0 {
		t.Errorf("want 0 for pending record, got %d", n)
	}

	// После MarkSent — считается
	if err := repo.MarkSent(ctx, userID, jobID); err != nil {
		t.Fatalf("MarkSent: %v", err)
	}
	n, err = repo.CountToday(ctx, userID)
	if err != nil {
		t.Fatalf("CountToday after MarkSent: %v", err)
	}
	if n != 1 {
		t.Errorf("want 1 after MarkSent, got %d", n)
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
