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
	ensurePendingFunc func(ctx context.Context, userID, jobID int64, score float64, finalScore float64, rankerVersion string, reasonCodes []string, whyItFits string) (bool, bool, error)
	markSentFunc      func(ctx context.Context, userID, jobID int64) error
	markFailedFunc    func(ctx context.Context, userID, jobID int64) error
	deleteFunc        func(ctx context.Context, userID, jobID int64) error
	sentRecentlyFunc  func(ctx context.Context, userID int64, within time.Duration) (bool, error)
	countTodayFunc    func(ctx context.Context, userID int64) (int, error)
}

func (m *mockNotifRepo) EnsurePending(ctx context.Context, userID, jobID int64, score float64, finalScore float64, rankerVersion string, reasonCodes []string, whyItFits string) (bool, bool, error) {
	if m.ensurePendingFunc != nil {
		return m.ensurePendingFunc(ctx, userID, jobID, score, finalScore, rankerVersion, reasonCodes, whyItFits)
	}
	return true, true, nil // default: новое, нужно отправить
}

func (m *mockNotifRepo) GetPendingForUser(ctx context.Context, userID int64) ([]port.PendingNotification, error) {
	return nil, nil
}

func (m *mockNotifRepo) MarkSent(ctx context.Context, userID, jobID int64) error {
	if m.markSentFunc != nil {
		return m.markSentFunc(ctx, userID, jobID)
	}
	return nil
}

func (m *mockNotifRepo) MarkFailed(ctx context.Context, userID, jobID int64) error {
	if m.markFailedFunc != nil {
		return m.markFailedFunc(ctx, userID, jobID)
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

func (m *mockNotifRepo) CancelPendingByJobIDs(ctx context.Context, jobIDs []int64) (int64, error) {
	_ = ctx
	_ = jobIDs
	return 0, nil
}

type permanentNotifyError struct {
	msg string
}

func (e permanentNotifyError) Error() string {
	return e.msg
}

func (e permanentNotifyError) Permanent() bool {
	return true
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

func (m *mockUserRepo) Save(ctx context.Context, telegramID int64) (int64, bool, error) {
	return 1, true, nil
}
func (m *mockUserRepo) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	return nil, nil
}
func (m *mockUserRepo) UpdateProfile(ctx context.Context, userID int64, text string) error {
	return nil
}
func (m *mockUserRepo) UpdateProfileScoped(ctx context.Context, userID int64, text string) error {
	return nil
}
func (m *mockUserRepo) UpdateNotifyHourScoped(ctx context.Context, userID int64, hour int) error {
	return nil
}
func (m *mockUserRepo) GetPreferencesScoped(ctx context.Context, userID int64) (*domain.UserPreferences, error) {
	return &domain.UserPreferences{}, nil
}
func (m *mockUserRepo) UpsertPreferencesScoped(ctx context.Context, userID int64, prefs domain.UserPreferences) error {
	return nil
}
func (m *mockUserRepo) GetProUsersWithNotifyHour(ctx context.Context, hour int) ([]int64, error) {
	return nil, nil
}

type mockJobRepo struct {
	getByIDFunc  func(ctx context.Context, id int64) (*domain.Job, error)
	getByIDsFunc func(ctx context.Context, ids []int64) (map[int64]*domain.Job, error)
}

func (m *mockJobRepo) GetByID(ctx context.Context, id int64) (*domain.Job, error) {
	if m.getByIDFunc != nil {
		return m.getByIDFunc(ctx, id)
	}
	return &domain.Job{ID: id, Title: "Job", URL: "https://kwork.ru/p/1"}, nil
}

func (m *mockJobRepo) GetByIDs(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
	if m.getByIDsFunc != nil {
		return m.getByIDsFunc(ctx, ids)
	}
	result := make(map[int64]*domain.Job, len(ids))
	for _, id := range ids {
		result[id] = &domain.Job{ID: id, Title: "Job", URL: "https://kwork.ru/p/1"}
	}
	return result, nil
}

func (m *mockJobRepo) Save(ctx context.Context, job *domain.Job) (int64, error)  { return 1, nil }
func (m *mockJobRepo) ExistsByURL(ctx context.Context, url string) (bool, error) { return false, nil }
func (m *mockJobRepo) GetUnembeddedIDs(ctx context.Context, limit int) ([]int64, error) {
	return nil, nil
}
func (m *mockJobRepo) TouchSeenAt(ctx context.Context, url string) error { return nil }
func (m *mockJobRepo) ExpireStaleJobs(ctx context.Context, days int) ([]int64, error) {
	return nil, nil
}
func (m *mockJobRepo) ExpireByURL(ctx context.Context, url string) ([]int64, error) {
	_ = ctx
	_ = url
	return nil, nil
}

type mockNotifier struct {
	sendFunc func(ctx context.Context, telegramID int64, p port.NotifyPayload) error
}

func (m *mockNotifier) Send(ctx context.Context, telegramID int64, p port.NotifyPayload) error {
	if m.sendFunc != nil {
		return m.sendFunc(ctx, telegramID, p)
	}
	return nil
}

type mockProductEventRepo struct {
	recordFunc func(ctx context.Context, event port.ProductEvent) error
	events     []port.ProductEvent
}

func (m *mockProductEventRepo) Record(ctx context.Context, event port.ProductEvent) error {
	m.events = append(m.events, event)
	if m.recordFunc != nil {
		return m.recordFunc(ctx, event)
	}
	return nil
}

func TestSendNotification_Execute_RateLimited(t *testing.T) {
	sent := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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
	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
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
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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
	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
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
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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
	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
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
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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
	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
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
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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
	err := uc.Execute(context.Background(), 1, 1, 0.85, 0.85, "v2", nil, "")
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

func TestSendNotification_Execute_RecordsProductEvent(t *testing.T) {
	eventRepo := &mockProductEventRepo{}
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{getByIDFunc: func(context.Context, int64) (*domain.Job, error) {
			return &domain.Job{ID: 1, Source: "kwork", Title: "T", URL: "https://kwork.ru/p/1"}, nil
		}},
		&mockNotifier{},
		5*time.Minute,
		5,
	).WithProductEventRepo(eventRepo)

	err := uc.Execute(context.Background(), 1, 1, 0.85, 0.9, "v3", []string{"top_match"}, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if len(eventRepo.events) != 1 {
		t.Fatalf("events len = %d, want 1", len(eventRepo.events))
	}
	event := eventRepo.events[0]
	if event.Type != port.ProductEventNotificationSent {
		t.Fatalf("event type = %q, want %q", event.Type, port.ProductEventNotificationSent)
	}
	if event.Source != "kwork" {
		t.Fatalf("event source = %q, want kwork", event.Source)
	}
	if got := event.Properties["delivery_mode"]; got != "single" {
		t.Fatalf("delivery_mode = %v, want single", got)
	}
}

func TestSendNotification_Execute_WhyItFits_Passed(t *testing.T) {
	var gotPayload port.NotifyPayload
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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
	err := uc.Execute(context.Background(), 1, 1, 0.85, 0.85, "v2", []string{"strong_similarity"}, "Веб-проект со стеком python, react.")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if gotPayload.WhyItFits != "Веб-проект со стеком python, react." {
		t.Errorf("WhyItFits not passed through: got %q", gotPayload.WhyItFits)
	}
	if gotPayload.FinalScore != 0.85 {
		t.Errorf("FinalScore=%v want 0.85", gotPayload.FinalScore)
	}
	if gotPayload.RankerVersion != "v2" {
		t.Errorf("RankerVersion=%q want v2", gotPayload.RankerVersion)
	}
	if len(gotPayload.ReasonCodes) != 1 || gotPayload.ReasonCodes[0] != "strong_similarity" {
		t.Errorf("ReasonCodes=%v want [strong_similarity]", gotPayload.ReasonCodes)
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
	err := uc.Execute(context.Background(), 999, 1, 0.9, 0.9, "v2", nil, "")
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

	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
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

	err := uc.Execute(context.Background(), 1, 999, 0.9, 0.9, "v2", nil, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
}

func TestSendNotification_Execute_ExpiredJobSkipped(t *testing.T) {
	markSentCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			markSentFunc: func(context.Context, int64, int64) error {
				markSentCalled = true
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{getByIDFunc: func(context.Context, int64) (*domain.Job, error) {
			// Expired jobs are filtered in postgres GetByID and surface as not found.
			return nil, nil
		}},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			t.Error("must not call Send when job is expired")
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.Execute(context.Background(), 1, 999, 0.9, 0.9, "v2", nil, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if markSentCalled {
		t.Fatal("must not mark sent when job is expired")
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

	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
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
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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

	err := uc.Execute(context.Background(), 1, 1, 0.85, 0.85, "v2", nil, "")
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

func TestSendNotification_Execute_PermanentSendFailed_MarksFailed(t *testing.T) {
	markFailedCalled := false
	deleteCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			markFailedFunc: func(context.Context, int64, int64) error {
				markFailedCalled = true
				return nil
			},
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
			return permanentNotifyError{msg: "telegram api: http 400"}
		}},
		5*time.Minute,
		5,
	)

	if err := uc.Execute(context.Background(), 1, 1, 0.85, 0.85, "v2", nil, ""); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !markFailedCalled {
		t.Fatal("must mark notification failed on permanent telegram error")
	}
	if deleteCalled {
		t.Fatal("must not delete notification on permanent telegram error")
	}
}

// TestSendNotification_Execute_RateLimited_DeletesRecord проверяет, что при rate limit
// только что созданная pending-запись удаляется.
func TestSendNotification_Execute_RateLimited_DeletesRecord(t *testing.T) {
	deleteCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
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

	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
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
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _ float64, _ float64, _ string, _ []string, _ string) (bool, bool, error) {
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
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _ float64, _ float64, _ string, _ []string, _ string) (bool, bool, error) {
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
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _ float64, _ float64, _ string, _ []string, _ string) (bool, bool, error) {
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

// TestSendNotification_ExecuteBatch_SingleJobsQuery проверяет, что для батча из N job
// GetByIDs вызывается ровно один раз (а не N раз GetByID).
func TestSendNotification_ExecuteBatch_SingleJobsQuery(t *testing.T) {
	getByIDsCalls := 0
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
		},
		&mockUserRepo{},
		&mockJobRepo{
			getByIDsFunc: func(_ context.Context, ids []int64) (map[int64]*domain.Job, error) {
				getByIDsCalls++
				result := make(map[int64]*domain.Job, len(ids))
				for _, id := range ids {
					result[id] = &domain.Job{ID: id, Title: "J", URL: "https://kwork.ru/p/1"}
				}
				return result, nil
			},
		},
		&mockNotifier{},
		5*time.Minute,
		5,
	)

	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10}, {JobID: 20}, {JobID: 30}, {JobID: 40}, {JobID: 50},
	}, 7.0)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if getByIDsCalls != 1 {
		t.Fatalf("GetByIDs called %d times, want exactly 1", getByIDsCalls)
	}
}

func TestSendNotification_ExecuteBatch_SkipsExpiredJobs(t *testing.T) {
	markedJobIDs := make([]int64, 0, 2)
	var gotPayload port.NotifyPayload

	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			markSentFunc: func(_ context.Context, _ int64, jobID int64) error {
				markedJobIDs = append(markedJobIDs, jobID)
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 777}, nil
		}},
		&mockJobRepo{
			getByIDsFunc: func(_ context.Context, ids []int64) (map[int64]*domain.Job, error) {
				return map[int64]*domain.Job{
					10: {ID: 10, Title: "Active", URL: "https://kwork.ru/p/10"},
					// Job 20 is expired and filtered by postgres GetByIDs.
				}, nil
			},
		},
		&mockNotifier{sendFunc: func(_ context.Context, _ int64, p port.NotifyPayload) error {
			gotPayload = p
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, Rank: 1, FinalScore: 0.91},
		{JobID: 20, Rank: 2, FinalScore: 0.82},
	}, 8.4)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if len(gotPayload.Batch) != 1 {
		t.Fatalf("batch items=%d, want 1", len(gotPayload.Batch))
	}
	if gotPayload.Batch[0].Job == nil || gotPayload.Batch[0].Job.ID != 10 {
		t.Fatalf("sent wrong batch payload: %+v", gotPayload.Batch)
	}
	if len(markedJobIDs) != 1 || markedJobIDs[0] != 10 {
		t.Fatalf("marked jobs=%v, want [10]", markedJobIDs)
	}
}

func TestSendNotification_ExecuteBatch_SortsByFinalScore(t *testing.T) {
	var gotPayload port.NotifyPayload
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 777}, nil
		}},
		&mockJobRepo{},
		&mockNotifier{sendFunc: func(_ context.Context, _ int64, p port.NotifyPayload) error {
			gotPayload = p
			return nil
		}},
		5*time.Minute,
		5,
	)

	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, Rank: 1, FinalScore: 0.61, ReasonCodes: []string{"standard_recency"}},
		{JobID: 20, Rank: 2, FinalScore: 0.93, ReasonCodes: []string{"high_similarity"}},
		{JobID: 30, Rank: 3, FinalScore: 0.72, ReasonCodes: []string{"fresh_job"}},
	}, 8.4)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if len(gotPayload.Batch) != 3 {
		t.Fatalf("batch items=%d want 3", len(gotPayload.Batch))
	}
	if gotPayload.Batch[0].Job == nil || gotPayload.Batch[0].Job.ID != 20 {
		t.Fatalf("first batch job=%+v want job 20", gotPayload.Batch[0].Job)
	}
	if gotPayload.Batch[0].Rank != 1 || gotPayload.Batch[1].Rank != 2 || gotPayload.Batch[2].Rank != 3 {
		t.Fatalf("unexpected ranks: %+v", gotPayload.Batch)
	}
}

func TestSendNotification_ExecuteBatch_PermanentSendFailed_MarksFailed(t *testing.T) {
	markedFailed := make(map[int64]bool)
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _ float64, _ float64, _ string, _ []string, _ string) (bool, bool, error) {
				return true, true, nil
			},
			sentRecentlyFunc: func(context.Context, int64, time.Duration) (bool, error) { return false, nil },
			countTodayFunc:   func(context.Context, int64) (int, error) { return 0, nil },
			markFailedFunc: func(_ context.Context, _ int64, jobID int64) error {
				markedFailed[jobID] = true
				return nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 777}, nil
		}},
		&mockJobRepo{
			getByIDsFunc: func(_ context.Context, ids []int64) (map[int64]*domain.Job, error) {
				result := make(map[int64]*domain.Job, len(ids))
				for _, id := range ids {
					result[id] = &domain.Job{ID: id, Title: "Job", URL: "https://kwork.ru/p/1"}
				}
				return result, nil
			},
		},
		&mockNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error {
			return permanentNotifyError{msg: "telegram api: http 403"}
		}},
		5*time.Minute,
		5,
	)

	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, FinalScore: 0.9, Rank: 1},
		{JobID: 20, FinalScore: 0.8, Rank: 2},
	}, 0.9)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if !markedFailed[10] || !markedFailed[20] {
		t.Fatalf("must mark all batch items failed, got %#v", markedFailed)
	}
}
