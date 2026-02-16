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
	recordFunc       func(ctx context.Context, userID, jobID int64, score float64) (bool, error)
	deleteFunc       func(ctx context.Context, userID, jobID int64) error
	sentRecentlyFunc func(ctx context.Context, userID int64, within time.Duration) (bool, error)
	countTodayFunc   func(ctx context.Context, userID int64) (int, error)
}

func (m *mockNotifRepo) Record(ctx context.Context, userID, jobID int64, score float64) (bool, error) {
	if m.recordFunc != nil {
		return m.recordFunc(ctx, userID, jobID, score)
	}
	return true, nil
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
		&mockNotifRepo{sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) {
			return true, nil
		}},
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
	err := uc.Execute(context.Background(), 1, 1, 0.9)
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
	err := uc.Execute(context.Background(), 1, 1, 0.9)
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if sent {
		t.Error("must not send when daily limit (5) reached")
	}
}

func TestSendNotification_Execute_DuplicateSkipped(t *testing.T) {
	sent := false
	uc := NewSendNotification(
		&mockNotifRepo{
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			recordFunc:       func(context.Context, int64, int64, float64) (bool, error) { return false, nil },
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
	err := uc.Execute(context.Background(), 1, 1, 0.9)
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if sent {
		t.Error("must not send when Record returns false (duplicate)")
	}
}

func TestSendNotification_Execute_Success(t *testing.T) {
	sent := false
	uc := NewSendNotification(
		&mockNotifRepo{
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			recordFunc:       func(context.Context, int64, int64, float64) (bool, error) { return true, nil },
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
	err := uc.Execute(context.Background(), 1, 1, 0.85)
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !sent {
		t.Error("must send when all checks pass")
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
	err := uc.Execute(context.Background(), 999, 1, 0.9)
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

	err := uc.Execute(context.Background(), 1, 1, 0.9)
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

	err := uc.Execute(context.Background(), 1, 999, 0.9)
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

	err := uc.Execute(context.Background(), 1, 1, 0.9)
	if err == nil {
		t.Fatal("want error on job lookup failure")
	}
	if !strings.Contains(err.Error(), "get job by id") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestSendNotification_Execute_SendFailed_RollbackRecord(t *testing.T) {
	deleteCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			recordFunc:       func(context.Context, int64, int64, float64) (bool, error) { return true, nil },
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

	err := uc.Execute(context.Background(), 1, 1, 0.85)
	if err == nil {
		t.Fatal("want error when notifier send fails")
	}
	if !deleteCalled {
		t.Fatal("must rollback notification record on send failure")
	}
}

func TestSendNotification_Execute_SendFailed_RollbackFails(t *testing.T) {
	uc := NewSendNotification(
		&mockNotifRepo{
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			recordFunc:       func(context.Context, int64, int64, float64) (bool, error) { return true, nil },
			deleteFunc: func(context.Context, int64, int64) error {
				return errors.New("rollback db error")
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

	err := uc.Execute(context.Background(), 1, 1, 0.85)
	if err == nil {
		t.Fatal("want joined error when send and rollback fail")
	}
	if !strings.Contains(err.Error(), "telegram down") {
		t.Fatalf("error must contain send failure, got: %v", err)
	}
	if !strings.Contains(err.Error(), "rollback notification record") {
		t.Fatalf("error must contain rollback context, got: %v", err)
	}
}
