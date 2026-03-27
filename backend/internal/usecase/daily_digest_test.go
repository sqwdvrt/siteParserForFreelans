package usecase

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type digestUserRepo struct {
	getByIDFunc                 func(ctx context.Context, userID int64) (*domain.User, error)
	getProUsersWithNotifyHourFn func(ctx context.Context, hour int) ([]int64, error)
}

func (m *digestUserRepo) Save(ctx context.Context, telegramID int64) (int64, bool, error) {
	return 0, false, nil
}

func (m *digestUserRepo) GetByID(ctx context.Context, userID int64) (*domain.User, error) {
	if m.getByIDFunc != nil {
		return m.getByIDFunc(ctx, userID)
	}
	return &domain.User{ID: userID, TelegramID: 1000 + userID}, nil
}

func (m *digestUserRepo) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	return nil, nil
}

func (m *digestUserRepo) UpdateProfile(ctx context.Context, userID int64, text string) error {
	return nil
}

func (m *digestUserRepo) UpdateProfileScoped(ctx context.Context, userID int64, text string) error {
	return nil
}

func (m *digestUserRepo) UpdateNotifyHourScoped(ctx context.Context, userID int64, hour int) error {
	return nil
}

func (m *digestUserRepo) GetPreferencesScoped(ctx context.Context, userID int64) (*domain.UserPreferences, error) {
	return &domain.UserPreferences{}, nil
}

func (m *digestUserRepo) UpsertPreferencesScoped(ctx context.Context, userID int64, prefs domain.UserPreferences) error {
	return nil
}

func (m *digestUserRepo) GetProUsersWithNotifyHour(ctx context.Context, hour int) ([]int64, error) {
	if m.getProUsersWithNotifyHourFn != nil {
		return m.getProUsersWithNotifyHourFn(ctx, hour)
	}
	return nil, nil
}

type digestNotifRepo struct {
	countTodayFunc      func(ctx context.Context, userID int64) (int, error)
	claimPendingFunc    func(ctx context.Context, userID int64, limit int) ([]port.PendingNotification, error)
	releaseClaimsFunc   func(ctx context.Context, userID int64, jobIDs []int64) error
	reclaimClaimsFunc   func(ctx context.Context, olderThan time.Duration) (int64, error)
	markDispatchedFunc  func(ctx context.Context, userID, jobID int64) error
	markSentFunc        func(ctx context.Context, userID, jobID int64) error
	markDispatchedCalls []int64
	markSentCalls       []int64
	releasedJobIDs      []int64
	reclaimCalled       bool
}

func (m *digestNotifRepo) EnsurePending(ctx context.Context, userID, jobID int64, matchScore float64, finalScore float64, rankerVersion string, reasonCodes []string, whyItFits string) (bool, bool, error) {
	return false, false, nil
}

func (m *digestNotifRepo) MarkDispatched(ctx context.Context, userID, jobID int64) error {
	m.markDispatchedCalls = append(m.markDispatchedCalls, jobID)
	if m.markDispatchedFunc != nil {
		return m.markDispatchedFunc(ctx, userID, jobID)
	}
	return nil
}

func (m *digestNotifRepo) MarkSent(ctx context.Context, userID, jobID int64) error {
	m.markSentCalls = append(m.markSentCalls, jobID)
	if m.markSentFunc != nil {
		return m.markSentFunc(ctx, userID, jobID)
	}
	return nil
}

func (m *digestNotifRepo) MarkFailed(ctx context.Context, userID, jobID int64) error {
	_ = ctx
	_ = userID
	_ = jobID
	return nil
}

func (m *digestNotifRepo) Delete(ctx context.Context, userID, jobID int64) error { return nil }
func (m *digestNotifRepo) SentRecently(ctx context.Context, userID int64, within time.Duration) (bool, error) {
	return false, nil
}

func (m *digestNotifRepo) CountToday(ctx context.Context, userID int64) (int, error) {
	if m.countTodayFunc != nil {
		return m.countTodayFunc(ctx, userID)
	}
	return 0, nil
}

func (m *digestNotifRepo) ClaimPendingDigestNotifications(
	ctx context.Context,
	userID int64,
	limit int,
) ([]port.PendingNotification, error) {
	if m.claimPendingFunc != nil {
		return m.claimPendingFunc(ctx, userID, limit)
	}
	return nil, nil
}

func (m *digestNotifRepo) ReleasePendingDigestNotifications(ctx context.Context, userID int64, jobIDs []int64) error {
	m.releasedJobIDs = append([]int64(nil), jobIDs...)
	if m.releaseClaimsFunc != nil {
		return m.releaseClaimsFunc(ctx, userID, jobIDs)
	}
	return nil
}

func (m *digestNotifRepo) ReclaimStaleDigestClaims(ctx context.Context, olderThan time.Duration) (int64, error) {
	m.reclaimCalled = true
	if m.reclaimClaimsFunc != nil {
		return m.reclaimClaimsFunc(ctx, olderThan)
	}
	return 0, nil
}

func (m *digestNotifRepo) GetPendingForUser(ctx context.Context, userID int64) ([]port.PendingNotification, error) {
	return m.ClaimPendingDigestNotifications(ctx, userID, 0)
}

func (m *digestNotifRepo) CancelPendingByJobIDs(ctx context.Context, jobIDs []int64) (int64, error) {
	_ = ctx
	_ = jobIDs
	return 0, nil
}

type digestJobRepo struct {
	getByIDsFunc func(ctx context.Context, ids []int64) (map[int64]*domain.Job, error)
}

func (m *digestJobRepo) Save(ctx context.Context, job *domain.Job) (int64, error)   { return 0, nil }
func (m *digestJobRepo) GetByID(ctx context.Context, id int64) (*domain.Job, error) { return nil, nil }

func (m *digestJobRepo) GetByIDs(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
	if m.getByIDsFunc != nil {
		return m.getByIDsFunc(ctx, ids)
	}
	result := make(map[int64]*domain.Job, len(ids))
	for _, id := range ids {
		result[id] = &domain.Job{ID: id, Title: "job"}
	}
	return result, nil
}

func (m *digestJobRepo) ExistsByURL(ctx context.Context, url string) (bool, error) { return false, nil }
func (m *digestJobRepo) GetUnembeddedIDs(ctx context.Context, limit int) ([]int64, error) {
	return nil, nil
}
func (m *digestJobRepo) TouchSeenAt(ctx context.Context, url string) error { return nil }
func (m *digestJobRepo) ExpireStaleJobs(ctx context.Context, days int) ([]int64, error) {
	return nil, nil
}

func (m *digestJobRepo) ExpireByURL(ctx context.Context, url string) ([]int64, error) {
	_ = ctx
	_ = url
	return nil, nil
}

type digestNotifier struct {
	sendFunc func(ctx context.Context, telegramID int64, payload port.NotifyPayload) error
	sentTo   int64
	payload  port.NotifyPayload
}

func (m *digestNotifier) Send(ctx context.Context, telegramID int64, payload port.NotifyPayload) error {
	m.sentTo = telegramID
	m.payload = payload
	if m.sendFunc != nil {
		return m.sendFunc(ctx, telegramID, payload)
	}
	return nil
}

func TestNewDailyDigestUsesDefaultMaxPerDay(t *testing.T) {
	uc := NewDailyDigest(&digestUserRepo{}, &digestNotifRepo{}, &digestJobRepo{}, &digestNotifier{}, 0)
	if uc.maxPerDay != defaultMaxPerDay {
		t.Fatalf("maxPerDay=%d want=%d", uc.maxPerDay, defaultMaxPerDay)
	}
}

func TestDailyDigestSendDigestForUserSuccess(t *testing.T) {
	notifRepo := &digestNotifRepo{
		countTodayFunc: func(context.Context, int64) (int, error) { return 1, nil },
		claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
			return []port.PendingNotification{
				{JobID: 1, MatchScore: 8.1, WhyItFits: "Go"},
				{JobID: 2, MatchScore: 7.5, WhyItFits: "PostgreSQL"},
				{JobID: 3, MatchScore: 7.0, WhyItFits: "missing"},
			}, nil
		},
	}
	jobRepo := &digestJobRepo{
		getByIDsFunc: func(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
			return map[int64]*domain.Job{
				1: {ID: 1, Title: "First"},
				2: {ID: 2, Title: "Second"},
			}, nil
		},
	}
	notifier := &digestNotifier{}
	uc := NewDailyDigest(&digestUserRepo{}, notifRepo, jobRepo, notifier, 3)

	if err := uc.sendDigestForUser(context.Background(), 42); err != nil {
		t.Fatalf("sendDigestForUser: %v", err)
	}
	if notifier.sentTo != 1042 {
		t.Fatalf("sentTo=%d", notifier.sentTo)
	}
	if len(notifier.payload.Batch) != 2 {
		t.Fatalf("batch len=%d", len(notifier.payload.Batch))
	}
	if len(notifRepo.markDispatchedCalls) != 2 || notifRepo.markDispatchedCalls[0] != 1 || notifRepo.markDispatchedCalls[1] != 2 {
		t.Fatalf("markDispatchedCalls=%v", notifRepo.markDispatchedCalls)
	}
	if len(notifRepo.markSentCalls) != 2 || notifRepo.markSentCalls[0] != 1 || notifRepo.markSentCalls[1] != 2 {
		t.Fatalf("markSentCalls=%v", notifRepo.markSentCalls)
	}
}

func TestDailyDigestSendDigestForUser_SkipsExpiredJobs(t *testing.T) {
	notifRepo := &digestNotifRepo{
		countTodayFunc: func(context.Context, int64) (int, error) { return 0, nil },
		claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
			return []port.PendingNotification{
				{JobID: 1, MatchScore: 8.1, WhyItFits: "Go"},
				{JobID: 2, MatchScore: 7.5, WhyItFits: "Expired"},
			}, nil
		},
	}
	jobRepo := &digestJobRepo{
		getByIDsFunc: func(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
			return map[int64]*domain.Job{
				1: {ID: 1, Title: "Active"},
				// Job 2 is expired and filtered by postgres GetByIDs.
			}, nil
		},
	}
	notifier := &digestNotifier{}
	uc := NewDailyDigest(&digestUserRepo{}, notifRepo, jobRepo, notifier, 3)

	if err := uc.sendDigestForUser(context.Background(), 42); err != nil {
		t.Fatalf("sendDigestForUser: %v", err)
	}
	if len(notifier.payload.Batch) != 1 {
		t.Fatalf("batch len=%d, want 1", len(notifier.payload.Batch))
	}
	if notifier.payload.Batch[0].Job == nil || notifier.payload.Batch[0].Job.ID != 1 {
		t.Fatalf("sent wrong digest payload: %+v", notifier.payload.Batch)
	}
	if len(notifRepo.markDispatchedCalls) != 1 || notifRepo.markDispatchedCalls[0] != 1 {
		t.Fatalf("markDispatchedCalls=%v, want [1]", notifRepo.markDispatchedCalls)
	}
	if len(notifRepo.markSentCalls) != 1 || notifRepo.markSentCalls[0] != 1 {
		t.Fatalf("markSentCalls=%v, want [1]", notifRepo.markSentCalls)
	}
}

func TestDailyDigestSendDigestForUserSkipsOnLimitsAndEmptyData(t *testing.T) {
	uc := NewDailyDigest(
		&digestUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) { return nil, nil }},
		&digestNotifRepo{countTodayFunc: func(context.Context, int64) (int, error) { return 5, nil }},
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)
	if err := uc.sendDigestForUser(context.Background(), 1); err != nil {
		t.Fatalf("nil user err=%v", err)
	}

	uc = NewDailyDigest(
		&digestUserRepo{},
		&digestNotifRepo{
			countTodayFunc: func(context.Context, int64) (int, error) { return 5, nil },
			claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
				t.Fatal("ClaimPendingDigestNotifications should not be called when limit is reached")
				return nil, nil
			},
		},
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)
	if err := uc.sendDigestForUser(context.Background(), 2); err != nil {
		t.Fatalf("limit reached err=%v", err)
	}

	uc = NewDailyDigest(
		&digestUserRepo{},
		&digestNotifRepo{
			claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) { return nil, nil },
		},
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)
	if err := uc.sendDigestForUser(context.Background(), 3); err != nil {
		t.Fatalf("empty pending err=%v", err)
	}
}

func TestDailyDigestSendDigestForUserPropagatesErrors(t *testing.T) {
	uc := NewDailyDigest(
		&digestUserRepo{getByIDFunc: func(context.Context, int64) (*domain.User, error) { return nil, errors.New("user repo failed") }},
		&digestNotifRepo{},
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)
	if err := uc.sendDigestForUser(context.Background(), 1); err == nil || err.Error() != "user repo failed" {
		t.Fatalf("user repo err=%v", err)
	}

	uc = NewDailyDigest(
		&digestUserRepo{},
		&digestNotifRepo{countTodayFunc: func(context.Context, int64) (int, error) { return 0, errors.New("count failed") }},
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)
	if err := uc.sendDigestForUser(context.Background(), 1); err == nil || err.Error() != "count failed" {
		t.Fatalf("count err=%v", err)
	}

	uc = NewDailyDigest(
		&digestUserRepo{},
		&digestNotifRepo{
			claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
				return []port.PendingNotification{{JobID: 1}}, nil
			},
		},
		&digestJobRepo{getByIDsFunc: func(context.Context, []int64) (map[int64]*domain.Job, error) { return nil, errors.New("jobs failed") }},
		&digestNotifier{},
		5,
	)
	if err := uc.sendDigestForUser(context.Background(), 1); err == nil || err.Error() != "jobs failed" {
		t.Fatalf("jobs err=%v", err)
	}

	notifRepo := &digestNotifRepo{
		claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
			return []port.PendingNotification{{JobID: 1}}, nil
		},
	}
	uc = NewDailyDigest(
		&digestUserRepo{},
		notifRepo,
		&digestJobRepo{},
		&digestNotifier{sendFunc: func(context.Context, int64, port.NotifyPayload) error { return errors.New("telegram failed") }},
		5,
	)
	if err := uc.sendDigestForUser(context.Background(), 1); err == nil || err.Error() != "telegram failed" {
		t.Fatalf("send err=%v", err)
	}
	if len(notifRepo.markDispatchedCalls) != 1 || notifRepo.markDispatchedCalls[0] != 1 {
		t.Fatalf("markDispatchedCalls=%v, want [1]", notifRepo.markDispatchedCalls)
	}
	if len(notifRepo.releasedJobIDs) != 0 {
		t.Fatalf("releasedJobIDs=%v, want none for at-most-once", notifRepo.releasedJobIDs)
	}
}

func TestDailyDigestExecuteContinuesAfterUserError(t *testing.T) {
	var processed []int64
	uc := NewDailyDigest(
		&digestUserRepo{
			getProUsersWithNotifyHourFn: func(context.Context, int) ([]int64, error) { return []int64{1, 2}, nil },
			getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
				processed = append(processed, userID)
				if userID == 1 {
					return nil, errors.New("broken user")
				}
				return &domain.User{ID: userID, TelegramID: 2000 + userID}, nil
			},
		},
		&digestNotifRepo{
			claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
				return []port.PendingNotification{{JobID: 10}}, nil
			},
		},
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)

	uc.Execute(context.Background())

	if len(processed) != 2 || processed[0] != 1 || processed[1] != 2 {
		t.Fatalf("processed=%v", processed)
	}
}

func TestDailyDigestExecute_ReclaimsStaleClaimsBeforeProcessingUsers(t *testing.T) {
	notifRepo := &digestNotifRepo{
		reclaimClaimsFunc: func(context.Context, time.Duration) (int64, error) { return 1, nil },
		claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
			return []port.PendingNotification{{JobID: 10}}, nil
		},
	}
	var processed []int64
	uc := NewDailyDigest(
		&digestUserRepo{
			getProUsersWithNotifyHourFn: func(context.Context, int) ([]int64, error) { return []int64{2}, nil },
			getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
				processed = append(processed, userID)
				return &domain.User{ID: userID, TelegramID: 2000 + userID}, nil
			},
		},
		notifRepo,
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)

	uc.Execute(context.Background())

	if notifRepo.reclaimCalled != true {
		t.Fatal("want stale digest claims reclaimed before processing users")
	}
	if len(processed) != 1 || processed[0] != 2 {
		t.Fatalf("processed=%v, want [2]", processed)
	}
}

func TestDailyDigestExecute_StopsWhenReclaimFails(t *testing.T) {
	notifRepo := &digestNotifRepo{
		reclaimClaimsFunc: func(context.Context, time.Duration) (int64, error) { return 0, errors.New("reclaim failed") },
		claimPendingFunc: func(context.Context, int64, int) ([]port.PendingNotification, error) {
			t.Fatal("ClaimPendingDigestNotifications must not run when reclaim fails")
			return nil, nil
		},
	}
	var processed []int64
	uc := NewDailyDigest(
		&digestUserRepo{
			getProUsersWithNotifyHourFn: func(context.Context, int) ([]int64, error) {
				processed = append(processed, 99)
				return []int64{1}, nil
			},
		},
		notifRepo,
		&digestJobRepo{},
		&digestNotifier{},
		5,
	)

	uc.Execute(context.Background())

	if notifRepo.reclaimCalled != true {
		t.Fatal("want reclaim attempted")
	}
	if len(processed) != 0 {
		t.Fatalf("processed=%v, want no users processed when reclaim fails", processed)
	}
}
