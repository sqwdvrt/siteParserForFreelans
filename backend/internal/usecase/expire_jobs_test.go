package usecase

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockExpireRepo struct {
	expired    int64
	expireErr  error
	calledDays int
}

func (m *mockExpireRepo) Save(_ context.Context, _ *domain.Job) (int64, error) { return 0, nil }
func (m *mockExpireRepo) GetByID(_ context.Context, _ int64) (*domain.Job, error) {
	return nil, nil
}
func (m *mockExpireRepo) GetByIDs(_ context.Context, _ []int64) (map[int64]*domain.Job, error) {
	return nil, nil
}
func (m *mockExpireRepo) ExistsByURL(_ context.Context, _ string) (bool, error) { return false, nil }
func (m *mockExpireRepo) TouchSeenAt(_ context.Context, _ string) error         { return nil }
func (m *mockExpireRepo) GetUnembeddedIDs(_ context.Context, _ int) ([]int64, error) {
	return nil, nil
}

func (m *mockExpireRepo) ExpireByURL(_ context.Context, _ string) ([]int64, error) {
	return nil, nil
}
func (m *mockExpireRepo) ExpireStaleJobs(_ context.Context, olderThanDays int) ([]int64, error) {
	m.calledDays = olderThanDays
	if m.expireErr != nil {
		return nil, m.expireErr
	}
	ids := make([]int64, m.expired)
	for i := range ids {
		ids[i] = int64(i + 1)
	}
	return ids, nil
}

type mockExpireNotifRepo struct {
	cancelErr       error
	cancelledJobIDs []int64
}

func (m *mockExpireNotifRepo) EnsurePending(_ context.Context, _, _ int64, _, _ float64, _ string, _ []string, _ string) (bool, bool, error) {
	return false, false, nil
}
func (m *mockExpireNotifRepo) MarkSent(_ context.Context, _, _ int64) error { return nil }
func (m *mockExpireNotifRepo) Delete(_ context.Context, _, _ int64) error   { return nil }
func (m *mockExpireNotifRepo) SentRecently(_ context.Context, _ int64, _ time.Duration) (bool, error) {
	return false, nil
}
func (m *mockExpireNotifRepo) CountToday(_ context.Context, _ int64) (int, error) { return 0, nil }
func (m *mockExpireNotifRepo) GetPendingForUser(_ context.Context, _ int64) ([]port.PendingNotification, error) {
	return nil, nil
}
func (m *mockExpireNotifRepo) CancelPendingByJobIDs(_ context.Context, jobIDs []int64) (int64, error) {
	m.cancelledJobIDs = append(m.cancelledJobIDs, jobIDs...)
	if m.cancelErr != nil {
		return 0, m.cancelErr
	}
	return int64(len(jobIDs)), nil
}

func TestExpireJobs_Execute_MarksStale(t *testing.T) {
	repo := &mockExpireRepo{expired: 5}
	notifRepo := &mockExpireNotifRepo{}
	uc := NewExpireJobs(repo, notifRepo, 14)

	n, err := uc.Execute(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if n != 5 {
		t.Errorf("want n=5, got %d", n)
	}
	if repo.calledDays != 14 {
		t.Errorf("want olderThanDays=14, got %d", repo.calledDays)
	}
}

func TestExpireJobs_Execute_NothingToExpire(t *testing.T) {
	repo := &mockExpireRepo{expired: 0}
	notifRepo := &mockExpireNotifRepo{}
	uc := NewExpireJobs(repo, notifRepo, 7)

	n, err := uc.Execute(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if n != 0 {
		t.Errorf("want n=0, got %d", n)
	}
}

func TestExpireJobs_Execute_RepoError(t *testing.T) {
	repo := &mockExpireRepo{expireErr: errors.New("db error")}
	notifRepo := &mockExpireNotifRepo{}
	uc := NewExpireJobs(repo, notifRepo, 14)

	_, err := uc.Execute(context.Background())
	if err == nil {
		t.Fatal("want error, got nil")
	}
}

func TestExpireJobs_Execute_CancelsPendingNotifications(t *testing.T) {
	repo := &mockExpireRepo{expired: 3}
	notifRepo := &mockExpireNotifRepo{}
	uc := NewExpireJobs(repo, notifRepo, 14)

	n, err := uc.Execute(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if n != 3 {
		t.Errorf("want n=3, got %d", n)
	}
	if len(notifRepo.cancelledJobIDs) != 3 {
		t.Errorf("want 3 cancelled job IDs, got %d", len(notifRepo.cancelledJobIDs))
	}
}

func TestExpireJobs_Execute_CancelNotifError_DoesNotFail(t *testing.T) {
	repo := &mockExpireRepo{expired: 2}
	notifRepo := &mockExpireNotifRepo{cancelErr: errors.New("db error")}
	uc := NewExpireJobs(repo, notifRepo, 14)

	// CancelPendingByJobIDs failure must not propagate — logged as warning only.
	n, err := uc.Execute(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if n != 2 {
		t.Errorf("want n=2, got %d", n)
	}
}
