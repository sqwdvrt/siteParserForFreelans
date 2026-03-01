package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/usecase"
)

func TestNextPopErrorBackoff_GrowthAndCap(t *testing.T) {
	if got := nextPopErrorBackoff(0); got != popErrorBackoffMin {
		t.Fatalf("first backoff = %v, want %v", got, popErrorBackoffMin)
	}

	b := popErrorBackoffMin
	for b < popErrorBackoffMax {
		next := nextPopErrorBackoff(b)
		if next < b {
			t.Fatalf("backoff decreased: prev=%v next=%v", b, next)
		}
		if next > popErrorBackoffMax {
			t.Fatalf("backoff exceeds max: %v > %v", next, popErrorBackoffMax)
		}
		b = next
		if b == popErrorBackoffMax {
			break
		}
	}

	if got := nextPopErrorBackoff(popErrorBackoffMax); got != popErrorBackoffMax {
		t.Fatalf("capped backoff = %v, want %v", got, popErrorBackoffMax)
	}
}

func TestWaitForBackoff_ContextCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	start := time.Now()
	ok := waitForBackoff(ctx, 10*time.Second)
	elapsed := time.Since(start)
	if ok {
		t.Fatal("waitForBackoff returned true for canceled context")
	}
	if elapsed > 100*time.Millisecond {
		t.Fatalf("waitForBackoff took too long after cancel: %v", elapsed)
	}
}

func TestNextNackRecoverBackoff_GrowthAndCap(t *testing.T) {
	if got := nextNackRecoverBackoff(0); got != nackRecoverBackoffMin {
		t.Fatalf("first backoff = %v, want %v", got, nackRecoverBackoffMin)
	}

	b := nackRecoverBackoffMin
	for b < nackRecoverBackoffMax {
		next := nextNackRecoverBackoff(b)
		if next < b {
			t.Fatalf("backoff decreased: prev=%v next=%v", b, next)
		}
		if next > nackRecoverBackoffMax {
			t.Fatalf("backoff exceeds max: %v > %v", next, nackRecoverBackoffMax)
		}
		b = next
		if b == nackRecoverBackoffMax {
			break
		}
	}

	if got := nextNackRecoverBackoff(nackRecoverBackoffMax); got != nackRecoverBackoffMax {
		t.Fatalf("capped backoff = %v, want %v", got, nackRecoverBackoffMax)
	}
}

func TestNotifierEnvParsing_FallbacksForInvalidValues(t *testing.T) {
	t.Setenv("NOTIFIER_MAX_RETRIES", "bad")
	t.Setenv("NOTIFIER_RETRY_BASE_WAIT", "not-duration")
	t.Setenv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", "-1")
	t.Setenv("NOTIFIER_BREAKER_OPEN_INTERVAL", "0s")
	t.Setenv("NOTIFIER_BREAKER_OPEN_JITTER", "1.5")

	if got := getPositiveIntEnv("NOTIFIER_MAX_RETRIES", 3); got != 3 {
		t.Fatalf("NOTIFIER_MAX_RETRIES fallback = %d, want 3", got)
	}
	if got := getDurationEnv("NOTIFIER_RETRY_BASE_WAIT", time.Second); got != time.Second {
		t.Fatalf("NOTIFIER_RETRY_BASE_WAIT fallback = %v, want 1s", got)
	}
	if got := getPositiveIntEnv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", 3); got != 3 {
		t.Fatalf("NOTIFIER_BREAKER_FAILURE_THRESHOLD fallback = %d, want 3", got)
	}
	if got := getDurationEnv("NOTIFIER_BREAKER_OPEN_INTERVAL", 30*time.Second); got != 30*time.Second {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_INTERVAL fallback = %v, want 30s", got)
	}
	if got := getFloatEnvInRange("NOTIFIER_BREAKER_OPEN_JITTER", 0.2, 0, 1); got != 0.2 {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_JITTER fallback = %v, want 0.2", got)
	}
}

func TestNotifierEnvParsing_UsesValidValues(t *testing.T) {
	t.Setenv("NOTIFIER_MAX_RETRIES", "8")
	t.Setenv("NOTIFIER_RETRY_BASE_WAIT", "750ms")
	t.Setenv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", "5")
	t.Setenv("NOTIFIER_BREAKER_OPEN_INTERVAL", "45s")
	t.Setenv("NOTIFIER_BREAKER_OPEN_JITTER", "0.35")

	if got := getPositiveIntEnv("NOTIFIER_MAX_RETRIES", 3); got != 8 {
		t.Fatalf("NOTIFIER_MAX_RETRIES parsed = %d, want 8", got)
	}
	if got := getDurationEnv("NOTIFIER_RETRY_BASE_WAIT", time.Second); got != 750*time.Millisecond {
		t.Fatalf("NOTIFIER_RETRY_BASE_WAIT parsed = %v, want 750ms", got)
	}
	if got := getPositiveIntEnv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", 3); got != 5 {
		t.Fatalf("NOTIFIER_BREAKER_FAILURE_THRESHOLD parsed = %d, want 5", got)
	}
	if got := getDurationEnv("NOTIFIER_BREAKER_OPEN_INTERVAL", 30*time.Second); got != 45*time.Second {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_INTERVAL parsed = %v, want 45s", got)
	}
	if got := getFloatEnvInRange("NOTIFIER_BREAKER_OPEN_JITTER", 0.2, 0, 1); got != 0.35 {
		t.Fatalf("NOTIFIER_BREAKER_OPEN_JITTER parsed = %v, want 0.35", got)
	}
}

func TestNotifierEnvParsing_EmptyValueUsesFallback(t *testing.T) {
	const key = "NOTIFIER_MAX_RETRIES"
	_ = os.Unsetenv(key)
	if got := getPositiveIntEnv(key, 4); got != 4 {
		t.Fatalf("empty env must use fallback, got %d", got)
	}
}

type stubUserRepo struct {
	getByID func(ctx context.Context, userID int64) (*domain.User, error)
}

func (s *stubUserRepo) Save(ctx context.Context, telegramID int64) (int64, error) { return 0, nil }
func (s *stubUserRepo) GetByID(ctx context.Context, userID int64) (*domain.User, error) {
	if s.getByID != nil {
		return s.getByID(ctx, userID)
	}
	return nil, nil
}
func (s *stubUserRepo) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	return nil, nil
}
func (s *stubUserRepo) UpdateProfile(ctx context.Context, userID int64, text string) error {
	return nil
}

type stubJobRepo struct {
	getByID func(ctx context.Context, jobID int64) (*domain.Job, error)
}

func (s *stubJobRepo) Save(ctx context.Context, job *domain.Job) (int64, error) { return 0, nil }
func (s *stubJobRepo) GetByID(ctx context.Context, id int64) (*domain.Job, error) {
	if s.getByID != nil {
		return s.getByID(ctx, id)
	}
	return nil, nil
}
func (s *stubJobRepo) GetByIDs(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
	result := make(map[int64]*domain.Job, len(ids))
	for _, id := range ids {
		if j, err := s.GetByID(ctx, id); err != nil {
			return nil, err
		} else if j != nil {
			result[id] = j
		}
	}
	return result, nil
}
func (s *stubJobRepo) ExistsByURL(ctx context.Context, url string) (bool, error) { return false, nil }

type captureNotifier struct {
	send func(ctx context.Context, telegramID int64, p port.NotifyPayload) error
}

func (s *captureNotifier) Send(ctx context.Context, telegramID int64, p port.NotifyPayload) error {
	if s.send != nil {
		return s.send(ctx, telegramID, p)
	}
	return nil
}

type stubNotifRepo struct {
	ensurePending func(ctx context.Context, userID, jobID int64, matchScore float64) (bool, bool, error)
	markSent      func(ctx context.Context, userID, jobID int64) error
	deleteFunc    func(ctx context.Context, userID, jobID int64) error
	sentRecently  func(ctx context.Context, userID int64, within time.Duration) (bool, error)
	countToday    func(ctx context.Context, userID int64) (int, error)
}

func (s *stubNotifRepo) EnsurePending(ctx context.Context, userID, jobID int64, matchScore float64) (bool, bool, error) {
	if s.ensurePending != nil {
		return s.ensurePending(ctx, userID, jobID, matchScore)
	}
	return true, true, nil
}

func (s *stubNotifRepo) MarkSent(ctx context.Context, userID, jobID int64) error {
	if s.markSent != nil {
		return s.markSent(ctx, userID, jobID)
	}
	return nil
}

func (s *stubNotifRepo) Delete(ctx context.Context, userID, jobID int64) error {
	if s.deleteFunc != nil {
		return s.deleteFunc(ctx, userID, jobID)
	}
	return nil
}

func (s *stubNotifRepo) SentRecently(ctx context.Context, userID int64, within time.Duration) (bool, error) {
	if s.sentRecently != nil {
		return s.sentRecently(ctx, userID, within)
	}
	return false, nil
}

func (s *stubNotifRepo) CountToday(ctx context.Context, userID int64) (int, error) {
	if s.countToday != nil {
		return s.countToday(ctx, userID)
	}
	return 0, nil
}

func TestSendBatchNotification_UserNotFound(t *testing.T) {
	called := false
	sendNotif := usecase.NewSendNotification(
		&stubNotifRepo{},
		&stubUserRepo{getByID: func(context.Context, int64) (*domain.User, error) { return nil, nil }},
		&stubJobRepo{},
		&captureNotifier{send: func(context.Context, int64, port.NotifyPayload) error {
			called = true
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := sendBatchNotification(
		context.Background(),
		sendNotif,
		port.MatchNotifyPayload{
			UserID: 1,
			Jobs:   []port.BatchJobItem{{JobID: 100, Rank: 1}},
		},
	)
	if err != nil {
		t.Fatalf("sendBatchNotification: %v", err)
	}
	if called {
		t.Fatal("notifier.Send must not be called when user not found")
	}
}

func TestSendBatchNotification_BuildsBatchPayload(t *testing.T) {
	var gotPayload port.NotifyPayload
	sendNotif := usecase.NewSendNotification(
		&stubNotifRepo{
			ensurePending: func(_ context.Context, _ int64, _ int64, _ float64) (bool, bool, error) {
				return true, true, nil
			},
		},
		&stubUserRepo{getByID: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 777}, nil
		}},
		&stubJobRepo{getByID: func(_ context.Context, jobID int64) (*domain.Job, error) {
			return &domain.Job{ID: jobID, Title: "DB title", URL: "https://kwork.ru/projects/1"}, nil
		}},
		&captureNotifier{send: func(_ context.Context, telegramID int64, p port.NotifyPayload) error {
			if telegramID != 777 {
				t.Fatalf("telegramID=%d, want 777", telegramID)
			}
			gotPayload = p
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := sendBatchNotification(
		context.Background(),
		sendNotif,
		port.MatchNotifyPayload{
			UserID:      1,
			CriticScore: 8.2,
			Jobs: []port.BatchJobItem{
				{JobID: 1, Title: "Payload title", WhyItFits: "Why", Rank: 1},
			},
		},
	)
	if err != nil {
		t.Fatalf("sendBatchNotification: %v", err)
	}
	if len(gotPayload.Batch) != 1 {
		t.Fatalf("batch items=%d, want 1", len(gotPayload.Batch))
	}
	if gotPayload.CriticScore != 8.2 {
		t.Fatalf("critic score=%.1f, want 8.2", gotPayload.CriticScore)
	}
	if gotPayload.Batch[0].Job == nil || gotPayload.Batch[0].Job.Title != "Payload title" {
		t.Fatalf("job title override failed, got %+v", gotPayload.Batch[0].Job)
	}
	if gotPayload.Batch[0].WhyItFits != "Why" {
		t.Fatalf("why_it_fits not propagated: %q", gotPayload.Batch[0].WhyItFits)
	}
}
