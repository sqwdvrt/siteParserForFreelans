package usecase

import (
	"context"
	"errors"
	"testing"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
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
func (m *mockExpireRepo) TouchSeenAt(_ context.Context, _ string) error          { return nil }
func (m *mockExpireRepo) GetUnembeddedIDs(_ context.Context, _ int) ([]int64, error) {
	return nil, nil
}
func (m *mockExpireRepo) ExpireStaleJobs(_ context.Context, olderThanDays int) (int64, error) {
	m.calledDays = olderThanDays
	return m.expired, m.expireErr
}

func TestExpireJobs_Execute_MarksStale(t *testing.T) {
	repo := &mockExpireRepo{expired: 5}
	uc := NewExpireJobs(repo, 14)

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
	uc := NewExpireJobs(repo, 7)

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
	uc := NewExpireJobs(repo, 14)

	_, err := uc.Execute(context.Background())
	if err == nil {
		t.Fatal("want error, got nil")
	}
}
