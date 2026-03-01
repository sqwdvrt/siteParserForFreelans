package usecase

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockNotifRepo struct {
	ensurePendingFunc func(ctx context.Context, userID, jobID int64, score float64) (bool, bool, error)
	markSentFunc      func(ctx context.Context, userID, jobID int64) error
	deleteFunc        func(ctx context.Context, userID, jobID int64) error
	sentRecentlyFunc  func(ctx context.Context, userID int64, within time.Duration) (bool, error)
	countTodayFunc    func(ctx context.Context, userID int64) (int, error)
}

func (m *mockNotifRepo) EnsurePending(ctx context.Context, userID, jobID int64, score float64) (bool, bool, error) {
	if m.ensurePendingFunc != nil {
		return m.ensurePendingFunc(ctx, userID, jobID, score)
	}
	return true, true, nil // default: новое, нужно отправить
}

func (m *mockNotifRepo) MarkSent(ctx context.Context, userID, jobID int64) error {
	if m.markSentFunc != nil {
		return m.markSentFunc(ctx, userID, jobID)
	}
	return nil
}

func (m *mockNotifRepo) Delete(ctx context.Context, userID, jobID int64) error {
	if m.deleteFunc != nil {
		return m.deleteFunc(ctx, userID, jobID)
	}
	return nil
}

func (m *mockNotifRepo) SentRecently(ctx context.Context, userID int64, within time.Duration) (bool, error) {
	if m.sentRecentlyFunc != nil {
		return m.sentRecentlyFunc(ctx, userID, within)
	}
	return false, nil
}

func (m *mockNotifRepo) CountToday(ctx context.Context, userID int64) (int, error) {
	if m.countTodayFunc != nil {
		return m.countTodayFunc(ctx, userID)
	}
	return 0, nil
}

type mockUserRepo struct {
	getByIDFunc func(ctx context.Context, userID int64) (*domain.User, error)
}

func (m *mockUserRepo) GetByID(ctx context.Context, userID int64) (*domain.User, error) {
	if m.getByIDFunc != nil {
		return m.getByIDFunc(ctx, userID)
	}
	return &domain.User{ID: 1, TelegramID: 123456}, nil
}

func (m *mockUserRepo) Save(ctx context.Context, telegramID int64) (int64, error) { return 1, nil }
func (m *mockUserRepo) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	return nil, nil
}
func (m *mockUserRepo) UpdateProfile(ctx context.Context, userID int64, text string) error {
	return nil
}

type mockJobRepo struct {
	getByIDFunc func(ctx context.Context, id int64) (*domain.Job, error)
}

func (m *mockJobRepo) GetByID(ctx context.Context, id int64) (*domain.Job, error) {
	if m.getByIDFunc != nil {
		return m.getByIDFunc(ctx, id)
	}
	return &domain.Job{ID: 1, Title: "Job", URL: "https://kwork.ru/p/1"}, nil
}

func (m *mockJobRepo) Save(ctx context.Context, job *domain.Job) (int64, error)  { return 1, nil }
func (m *mockJobRepo) ExistsByURL(ctx context.Context, url string) (bool, error) { return false, nil }

type mockNotifier struct {
	sendFunc func(ctx context.Context, telegramID int64, p port.NotifyPayload) error
}

func (m *mockNotifier) Send(ctx context.Context, telegramID int64, p port.NotifyPayload) error {
	if m.sendFunc != nil {
		return m.sendFunc(ctx, telegramID, p)
	}
	return nil
}

func TestSendNotification_Execute_RateLimited(t *testing.T) {
	sent := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return true, true, nil // новое уведомление
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) {
				return true, nil // rate limited
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 999}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			sent = true
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := uc.Execute(context.Background(), 1, 1, 0.9, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if sent {
		t.Error("must not send when rate limited")
	}
}

func TestSendNotification_Execute_DailyLimitReached(t *testing.T) {
	sent := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 5, nil },
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 999}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			sent = true
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := uc.Execute(context.Background(), 1, 1, 0.9, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if sent {
		t.Error("must not send when daily limit (5) reached")
	}
}

func TestSendNotification_Execute_AlreadySentSkipped(t *testing.T) {
	sent := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return false, false, nil // shouldSend=false → уже доставлено
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 999}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			sent = true
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := uc.Execute(context.Background(), 1, 1, 0.9, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if sent {
		t.Error("must not send when EnsurePending returns shouldSend=false (already sent)")
	}
}

func TestSendNotification_Execute_RetrySkipsRateLimit(t *testing.T) {
	sent := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return false, true, nil // wasInserted=false → retry, пропустить rate limit
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 999}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			sent = true
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := uc.Execute(context.Background(), 1, 1, 0.9, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !sent {
		t.Error("must send on retry even if rate limit would apply")
	}
}

func TestSendNotification_Execute_Success(t *testing.T) {
	sent := false
	markSentCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			markSentFunc: func(context.Context, int64, int64) error {
				markSentCalled = true
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{getByIDFunc: func(context.Context, int64) (*domain.Job, error) {
			return &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}, nil
		}},
		&mockNotifier{sendFunc: func(ctx context.Context, telegramID int64, p port.NotifyPayload) error {
			if telegramID != 888 {
				t.Errorf("telegram_id want 888, got %d", telegramID)
			}
			if p.Job == nil || p.Job.Title != "T" {
				t.Error("payload job missing or wrong")
			}
			sent = true
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := uc.Execute(context.Background(), 1, 1, 0.85, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !sent {
		t.Error("must send when all checks pass")
	}
	if !markSentCalled {
		t.Error("must call MarkSent after successful send")
	}
}

func TestSendNotification_Execute_WhyItFits_Passed(t *testing.T) {
	var gotPayload port.NotifyPayload
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
		},
		&mockUserRepo{},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(_ context.Context, _ int64, p port.NotifyPayload) error {
			gotPayload = p
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := uc.Execute(context.Background(), 1, 1, 0.85, "Веб-проект со стеком python, react.")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if gotPayload.WhyItFits != "Веб-проект со стеком python, react." {
		t.Errorf("WhyItFits not passed through: got %q", gotPayload.WhyItFits)
	}
}

func TestSendNotification_Execute_UserNotFound(t *testing.T) {
	uc := NewSendNotification(
		&mockNotifRepo{},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return nil, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			t.Error("must not call Send when user not found")
			return nil
		}},
		5*time.Minute,
		5,
	)
	err := uc.Execute(context.Background(), 999, 1, 0.9, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
}

func TestSendNotification_Execute_UserLookupError(t *testing.T) {
	uc := NewSendNotification(
		&mockNotifRepo{},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return nil, errors.New("db down")
		}},
		&mockJobRepo{},
		&mockNotifier{},
		5*time.Minute,
		5,
	)

	err := uc.Execute(context.Background(), 1, 1, 0.9, "")
	if err == nil {
		t.Fatal("want error on user lookup failure")
	}
	if !strings.Contains(err.Error(), "get user by id") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestSendNotification_Execute_JobNotFound(t *testing.T) {
	uc := NewSendNotification(
		&mockNotifRepo{},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{getByIDFunc: func(context.Context, int64) (*domain.Job, error) {
			return nil, nil
		}},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			t.Error("must not call Send when job not found")
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.Execute(context.Background(), 1, 999, 0.9, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
}

func TestSendNotification_Execute_JobLookupError(t *testing.T) {
	uc := NewSendNotification(
		&mockNotifRepo{},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{getByIDFunc: func(context.Context, int64) (*domain.Job, error) {
			return nil, errors.New("db down")
		}},
		&mockNotifier{},
		5*time.Minute,
		5,
	)

	err := uc.Execute(context.Background(), 1, 1, 0.9, "")
	if err == nil {
		t.Fatal("want error on job lookup failure")
	}
	if !strings.Contains(err.Error(), "get job by id") {
		t.Fatalf("unexpected error: %v", err)
	}
}

// TestSendNotification_Execute_SendFailed_KeepsPending проверяет, что при ошибке Telegram
// pending-запись НЕ удаляется (остаётся для retry через Redis Nack).
func TestSendNotification_Execute_SendFailed_KeepsPending(t *testing.T) {
	deleteCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			deleteFunc: func(context.Context, int64, int64) error {
				deleteCalled = true
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{getByIDFunc: func(context.Context, int64) (*domain.Job, error) {
			return &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}, nil
		}},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			return errors.New("telegram down")
		}},
		5*time.Minute,
		5,
	)

	err := uc.Execute(context.Background(), 1, 1, 0.85, "")
	if err == nil {
		t.Fatal("want error when notifier send fails")
	}
	if !strings.Contains(err.Error(), "telegram down") {
		t.Fatalf("error must contain send failure, got: %v", err)
	}
	if deleteCalled {
		t.Fatal("must NOT delete pending record on send failure — retry via Redis Nack")
	}
}

// TestSendNotification_Execute_RateLimited_DeletesRecord проверяет, что при rate limit
// только что созданная pending-запись удаляется.
func TestSendNotification_Execute_RateLimited_DeletesRecord(t *testing.T) {
	deleteCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64) (bool, bool, error) {
				return true, true, nil // wasInserted=true → проверяем rate limit
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) {
				return true, nil // rate limited
			},
			deleteFunc: func(context.Context, int64, int64) error {
				deleteCalled = true
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			t.Error("must not send when rate limited")
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.Execute(context.Background(), 1, 1, 0.9, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !deleteCalled {
		t.Fatal("must delete pending record when rate limited")
	}
}

func TestSendNotification_ExecuteBatch_RateLimited_DropsNewItems(t *testing.T) {
	sent := false
	deletedJobIDs := make([]int64, 0, 2)
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _ float64) (bool, bool, error) {
				return true, true, nil // new batch items
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return true, nil },
			deleteFunc: func(_ context.Context, _ int64, jobID int64) error {
				deletedJobIDs = append(deletedJobIDs, jobID)
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			sent = true
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, Rank: 1},
		{JobID: 20, Rank: 2},
	}, 7.2)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if sent {
		t.Fatal("must not send batch when all items are rate limited")
	}
	if len(deletedJobIDs) != 2 {
		t.Fatalf("deleted jobs=%d, want 2", len(deletedJobIDs))
	}
}

func TestSendNotification_ExecuteBatch_RetryBypassesRateLimit(t *testing.T) {
	sent := false
	sentRecentlyCalled := false
	markSentCalls := 0
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _ float64) (bool, bool, error) {
				return false, true, nil // retry items
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) {
				sentRecentlyCalled = true
				return false, nil
			},
			markSentFunc: func(context.Context, int64, int64) error {
				markSentCalls++
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(_ context.Context, _ int64, p port.NotifyPayload) error {
			sent = true
			if len(p.Batch) != 2 {
				t.Fatalf("batch items=%d, want 2", len(p.Batch))
			}
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, Rank: 1},
		{JobID: 20, Rank: 2},
	}, 7.2)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if !sent {
		t.Fatal("must send retry batch")
	}
	if sentRecentlyCalled {
		t.Fatal("must not check rate limit for retry-only batch")
	}
	if markSentCalls != 2 {
		t.Fatalf("mark sent calls=%d, want 2", markSentCalls)
	}
}

func TestSendNotification_ExecuteBatch_DailyLimit_TrimsNewItems(t *testing.T) {
	sent := false
	deletedJobIDs := make([]int64, 0, 2)
	markedJobIDs := make([]int64, 0, 2)
	ensureCalls := 0

	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _ float64) (bool, bool, error) {
				ensureCalls++
				switch ensureCalls {
				case 1:
					return true, true, nil // new
				case 2:
					return true, true, nil // new (will be trimmed)
				default:
					return false, true, nil // retry
				}
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 4, nil }, // maxPerDay=5 => keep 1 new
			deleteFunc: func(_ context.Context, _ int64, jobID int64) error {
				deletedJobIDs = append(deletedJobIDs, jobID)
				return nil
			},
			markSentFunc: func(_ context.Context, _ int64, jobID int64) error {
				markedJobIDs = append(markedJobIDs, jobID)
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(_ context.Context, _ int64, p port.NotifyPayload) error {
			sent = true
			if len(p.Batch) != 2 {
				t.Fatalf("batch items=%d, want 2", len(p.Batch))
			}
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, Rank: 1},
		{JobID: 20, Rank: 2},
		{JobID: 30, Rank: 3},
	}, 8.1)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if !sent {
		t.Fatal("must send trimmed batch")
	}
	if len(deletedJobIDs) != 1 || deletedJobIDs[0] != 20 {
		t.Fatalf("deleted jobs=%v, want [20]", deletedJobIDs)
	}
	if len(markedJobIDs) != 2 {
		t.Fatalf("marked jobs=%v, want 2 items", markedJobIDs)
	}
}
