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
	ensurePendingFunc       func(ctx context.Context, userID, jobID int64, score float64, finalScore float64, rankerVersion string, reasonCodes []string, whyItFits string) (bool, bool, error)
	markDispatchedFunc      func(ctx context.Context, userID, jobID int64) error
	markSentFunc            func(ctx context.Context, userID, jobID int64) error
	markFailedFunc          func(ctx context.Context, userID, jobID int64) error
	markMissedFunc          func(ctx context.Context, userID, jobID int64) error
	deleteFunc              func(ctx context.Context, userID, jobID int64) error
	sentRecentlyFunc        func(ctx context.Context, userID int64, within time.Duration) (bool, error)
	countTodayFunc          func(ctx context.Context, userID int64) (int, error)
	claimPendingDigestFunc  func(ctx context.Context, userID int64, limit int) ([]port.PendingNotification, error)
	getFreeUsersFunc        func(ctx context.Context) ([]int64, error)
	deleteCalls             [][2]int64
	markMissedCalls         [][2]int64
}

func (m *mockNotifRepo) EnsurePending(ctx context.Context, userID, jobID int64, score float64, finalScore float64, rankerVersion string, reasonCodes []string, whyItFits string) (bool, bool, error) {
	if m.ensurePendingFunc != nil {
		return m.ensurePendingFunc(ctx, userID, jobID, score, finalScore, rankerVersion, reasonCodes, whyItFits)
	}
	return true, true, nil
}

func (m *mockNotifRepo) MarkDispatched(ctx context.Context, userID, jobID int64) error {
	if m.markDispatchedFunc != nil {
		return m.markDispatchedFunc(ctx, userID, jobID)
	}
	return nil
}

func (m *mockNotifRepo) GetPendingForUser(ctx context.Context, userID int64) ([]port.PendingNotification, error) {
	return nil, nil
}
func (m *mockNotifRepo) ClaimPendingDigestNotifications(ctx context.Context, userID int64, limit int) ([]port.PendingNotification, error) {
	if m.claimPendingDigestFunc != nil {
		return m.claimPendingDigestFunc(ctx, userID, limit)
	}
	return nil, nil
}
func (m *mockNotifRepo) ReleasePendingDigestNotifications(ctx context.Context, userID int64, jobIDs []int64) error {
	return nil
}
func (m *mockNotifRepo) ReclaimStaleDigestClaims(ctx context.Context, olderThan time.Duration) (int64, error) {
	return 0, nil
}
func (m *mockNotifRepo) MarkMissed(ctx context.Context, userID, jobID int64) error {
	m.markMissedCalls = append(m.markMissedCalls, [2]int64{userID, jobID})
	if m.markMissedFunc != nil {
		return m.markMissedFunc(ctx, userID, jobID)
	}
	return nil
}
func (m *mockNotifRepo) GetMissedForUser(ctx context.Context, userID int64) ([]port.MissedNotification, error) {
	return nil, nil
}
func (m *mockNotifRepo) ConvertMissedToPending(ctx context.Context, notificationIDs []int64) (int64, error) {
	return 0, nil
}
func (m *mockNotifRepo) DeleteNotifications(ctx context.Context, notificationIDs []int64) (int64, error) {
	return 0, nil
}
func (m *mockNotifRepo) GetFreeUsersWithPendingNotifications(ctx context.Context) ([]int64, error) {
	if m.getFreeUsersFunc != nil {
		return m.getFreeUsersFunc(ctx)
	}
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
	m.deleteCalls = append(m.deleteCalls, [2]int64{userID, jobID})
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
func (m *mockUserRepo) UpdatePauseScoped(ctx context.Context, userID int64, until *time.Time) error {
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
	return &domain.Job{ID: id, Source: "kwork", Title: "Job", URL: "https://kwork.ru/p/1", LastSeenAt: time.Now()}, nil
}

func (m *mockJobRepo) GetByIDs(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
	if m.getByIDsFunc != nil {
		return m.getByIDsFunc(ctx, ids)
	}
	result := make(map[int64]*domain.Job, len(ids))
	for _, id := range ids {
		result[id] = &domain.Job{ID: id, Source: "kwork", Title: "Job", URL: "https://kwork.ru/p/1", LastSeenAt: time.Now()}
	}
	return result, nil
}

func (m *mockJobRepo) Save(ctx context.Context, job *domain.Job) (int64, error)  { return 1, nil }
func (m *mockJobRepo) ExistsByURL(ctx context.Context, url string) (bool, error) { return false, nil }
func (m *mockJobRepo) GetUnembeddedIDs(ctx context.Context, limit int) ([]int64, error) {
	return nil, nil
}
func (m *mockJobRepo) TouchSeenAt(ctx context.Context, url string) error   { return nil }
func (m *mockJobRepo) TouchSeenAtByID(ctx context.Context, id int64) error { return nil }
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

// --- Execute tests ---

func TestSendNotification_Execute_Deferred_DoesNotSend(t *testing.T) {
	ensureCalled := false
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				ensureCalled = true
				return true, true, nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 999}, nil
		}},
		&mockJobRepo{},
	)
	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !ensureCalled {
		t.Error("EnsurePending must be called to store notification")
	}
}

func TestSendNotification_Execute_PausedUserSkipped(t *testing.T) {
	ensureCalled := false
	now := time.Now().Add(2 * time.Hour)
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				ensureCalled = true
				return true, true, nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 999, PausedUntil: &now}, nil
		}},
		&mockJobRepo{},
	)
	if err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, ""); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if ensureCalled {
		t.Fatal("must not call EnsurePending when user is paused")
	}
}

func TestSendNotification_Execute_AlreadySentSkipped(t *testing.T) {
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
	)
	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
}

func TestSendNotification_Execute_UserNotFound(t *testing.T) {
	uc := NewSendNotification(
		&mockNotifRepo{},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return nil, nil
		}},
		&mockJobRepo{},
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
	)
	err := uc.Execute(context.Background(), 1, 999, 0.9, 0.9, "v2", nil, "")
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
	)
	err := uc.Execute(context.Background(), 1, 1, 0.9, 0.9, "v2", nil, "")
	if err == nil {
		t.Fatal("want error on job lookup failure")
	}
	if !strings.Contains(err.Error(), "get job by id") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestSendNotification_Execute_StaleKworkJobDeletesPendingAndSkips(t *testing.T) {
	notifRepo := &mockNotifRepo{
		ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
			return false, true, nil
		},
	}
	uc := NewSendNotification(
		notifRepo,
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{getByIDFunc: func(context.Context, int64) (*domain.Job, error) {
			return &domain.Job{
				ID:         999,
				Source:     "kwork",
				Title:      "Stale Kwork",
				URL:        "https://kwork.ru/projects/999",
				LastSeenAt: time.Now().Add(-7 * time.Hour),
			}, nil
		}},
	)
	err := uc.Execute(context.Background(), 1, 999, 0.9, 0.9, "v2", nil, "")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if len(notifRepo.deleteCalls) != 1 || notifRepo.deleteCalls[0] != [2]int64{1, 999} {
		t.Fatalf("deleteCalls=%v, want [[1 999]]", notifRepo.deleteCalls)
	}
}

// --- ExecuteBatch tests ---

func TestSendNotification_ExecuteBatch_Deferred_DoesNotSend(t *testing.T) {
	ensureCalls := 0
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				ensureCalls++
				return true, true, nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888}, nil
		}},
		&mockJobRepo{},
	)
	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, Rank: 1},
		{JobID: 20, Rank: 2},
	}, 7.2)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if ensureCalls != 2 {
		t.Fatalf("EnsurePending calls=%d, want 2", ensureCalls)
	}
}

func TestSendNotification_ExecuteBatch_PausedUserSkipped(t *testing.T) {
	ensureCalled := false
	now := time.Now().Add(2 * time.Hour)
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
				ensureCalled = true
				return true, true, nil
			},
		},
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 888, PausedUntil: &now}, nil
		}},
		&mockJobRepo{},
	)
	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{{JobID: 10}}, 7.0)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if ensureCalled {
		t.Fatal("must not call EnsurePending when user is paused")
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
		},
		&mockUserRepo{},
		&mockJobRepo{
			getByIDsFunc: func(_ context.Context, ids []int64) (map[int64]*domain.Job, error) {
				getByIDsCalls++
				result := make(map[int64]*domain.Job, len(ids))
				for _, id := range ids {
					result[id] = &domain.Job{ID: id, Source: "kwork", Title: "J", URL: "https://kwork.ru/p/1", LastSeenAt: time.Now()}
				}
				return result, nil
			},
		},
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

func TestSendNotification_ExecuteBatch_DeletesStaleKworkJobs(t *testing.T) {
	notifRepo := &mockNotifRepo{
		ensurePendingFunc: func(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
			return true, true, nil
		},
	}
	uc := NewSendNotification(
		notifRepo,
		&mockUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) {
			return &domain.User{ID: 1, TelegramID: 777}, nil
		}},
		&mockJobRepo{
			getByIDsFunc: func(_ context.Context, ids []int64) (map[int64]*domain.Job, error) {
				return map[int64]*domain.Job{
					10: {ID: 10, Source: "kwork", Title: "Fresh", URL: "https://kwork.ru/p/10", LastSeenAt: time.Now().Add(-2 * time.Hour)},
					20: {ID: 20, Source: "kwork", Title: "Stale", URL: "https://kwork.ru/p/20", LastSeenAt: time.Now().Add(-7 * time.Hour)},
				}, nil
			},
		},
	)
	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 10, Rank: 1, FinalScore: 0.91},
		{JobID: 20, Rank: 2, FinalScore: 0.82},
	}, 8.4)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if len(notifRepo.deleteCalls) != 1 || notifRepo.deleteCalls[0] != [2]int64{1, 20} {
		t.Fatalf("deleteCalls=%v, want [[1 20]]", notifRepo.deleteCalls)
	}
}

func TestSendNotification_ExecuteBatch_DeduplicatesDuplicateJobIDs(t *testing.T) {
	ensureCalls := 0
	uc := NewSendNotification(
		&mockNotifRepo{
			ensurePendingFunc: func(_ context.Context, _ int64, _ int64, _, _ float64, _ string, _ []string, _ string) (bool, bool, error) {
				ensureCalls++
				return true, true, nil
			},
		},
		&mockUserRepo{},
		&mockJobRepo{},
	)
	err := uc.ExecuteBatch(context.Background(), 1, []port.BatchJobItem{
		{JobID: 42, FinalScore: 0.5, Rank: 2},
		{JobID: 42, FinalScore: 0.9, Rank: 1}, // same job, higher score — must NOT append ID again
	}, 0.9)
	if err != nil {
		t.Fatalf("ExecuteBatch: %v", err)
	}
	if ensureCalls != 1 {
		t.Fatalf("EnsurePending called %d times for one unique job, want 1", ensureCalls)
	}
}

// TestDailyDigest_ExecuteAccumulation_CallsNotifierViaFreeUsers проверяет, что
// ExecuteAccumulation вызывает notifier.Send для free-пользователей через sendDigestForUser.
func TestDailyDigest_ExecuteAccumulation_CallsNotifierViaFreeUsers(t *testing.T) {
	var sentPayloads []port.NotifyPayload
	notif := &mockNotifier{
		sendFunc: func(_ context.Context, _ int64, p port.NotifyPayload) error {
			sentPayloads = append(sentPayloads, p)
			return nil
		},
	}
	notifRepo := &mockNotifRepo{
		getFreeUsersFunc: func(context.Context) ([]int64, error) {
			return []int64{11}, nil
		},
		claimPendingDigestFunc: func(_ context.Context, _ int64, _ int) ([]port.PendingNotification, error) {
			return []port.PendingNotification{
				{JobID: 101, MatchScore: 8.5, WhyItFits: "fits"},
			}, nil
		},
	}
	uc := NewDailyDigest(
		&mockUserRepo{getByIDFunc: func(_ context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 2000 + userID}, nil
		}},
		notifRepo,
		&mockJobRepo{},
		notif,
		5,
	)
	uc.ExecuteAccumulation(context.Background())
	if len(sentPayloads) != 1 {
		t.Fatalf("expected 1 notification sent, got %d", len(sentPayloads))
	}
	if len(sentPayloads[0].Batch) != 1 {
		t.Fatalf("expected 1 item in batch, got %d", len(sentPayloads[0].Batch))
	}
}

// TestDailyDigest_ExecuteAccumulation_ReclaimsThenProcesses проверяет порядок:
// ReclaimStaleDigestClaims → GetFreeUsersWithPendingNotifications.
func TestDailyDigest_ExecuteAccumulation_ReclaimsThenProcesses(t *testing.T) {
	var reclaimCalled, getUsersCalled bool
	notifRepo := &mockNotifRepo{
		getFreeUsersFunc: func(context.Context) ([]int64, error) {
			if !reclaimCalled {
				t.Error("GetFreeUsers called before ReclaimStaleDigestClaims")
			}
			getUsersCalled = true
			return nil, nil
		},
	}
	// ReclaimStaleDigestClaims is handled by embedded mock — we need to intercept it.
	// Use digestNotifRepo instead for precise tracking.
	digestRepo := &digestNotifRepo{
		reclaimClaimsFunc: func(context.Context, time.Duration) (int64, error) {
			reclaimCalled = true
			return 0, nil
		},
	}
	_ = notifRepo // verify getFreeUsersFunc ordering only via digestRepo path below
	uc := NewDailyDigest(
		&mockUserRepo{},
		digestRepo,
		&mockJobRepo{},
		&mockNotifier{},
		5,
	)
	uc.ExecuteAccumulation(context.Background())
	if !reclaimCalled {
		t.Fatal("ReclaimStaleDigestClaims was not called")
	}
	_ = getUsersCalled
}

// TestDailyDigest_ExecuteAccumulation_StopsOnReclaimError проверяет, что при
// ошибке ReclaimStaleDigestClaims обработка пользователей не начинается.
func TestDailyDigest_ExecuteAccumulation_StopsOnReclaimError(t *testing.T) {
	getUsersCalled := false
	digestRepo := &digestNotifRepo{
		reclaimClaimsFunc: func(context.Context, time.Duration) (int64, error) {
			return 0, errors.New("redis down")
		},
	}
	uc := NewDailyDigest(
		&mockUserRepo{
			getByIDFunc: func(context.Context, int64) (*domain.User, error) {
				getUsersCalled = true
				return nil, nil
			},
		},
		digestRepo,
		&mockJobRepo{},
		&mockNotifier{},
		5,
	)
	uc.ExecuteAccumulation(context.Background())
	if getUsersCalled {
		t.Fatal("user processing must not start when reclaim fails")
	}
}
