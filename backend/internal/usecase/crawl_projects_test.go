package usecase

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/tgchannel"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockFetcher struct {
	listHTML []byte
	listErr  error
	details  map[string][]byte
	failURLs map[string]error
	calls    []string
}

func (m *mockFetcher) Fetch(ctx context.Context, url string) ([]byte, error) {
	m.calls = append(m.calls, url)
	if err, ok := m.failURLs[url]; ok {
		return nil, err
	}
	if m.listErr != nil {
		return nil, m.listErr
	}
	if data, ok := m.details[url]; ok {
		return data, nil
	}
	return m.listHTML, nil
}

type mockExtractor struct {
	listURLs  []string
	listErr   error
	detail    *domain.Job
	detailErr error
}

func (m *mockExtractor) ExtractList(html []byte) ([]string, error) {
	if m.listErr != nil {
		return nil, m.listErr
	}
	return m.listURLs, nil
}

func (m *mockExtractor) ExtractDetail(html []byte, pageURL string) (*domain.Job, error) {
	if m.detailErr != nil {
		return nil, m.detailErr
	}
	if m.detail != nil {
		j := *m.detail
		j.URL = pageURL
		return &j, nil
	}
	return nil, nil
}

type mockRepo struct {
	exists          map[string]bool
	saveID          int64
	saveErr         error
	touchedURLs     []string
	expiredURLs     []string
	expiredIDsByURL map[string][]int64
}

func (m *mockRepo) Save(ctx context.Context, job *domain.Job) (int64, error) {
	if m.saveErr != nil {
		return 0, m.saveErr
	}
	return m.saveID, nil
}

func (m *mockRepo) GetByID(ctx context.Context, id int64) (*domain.Job, error) {
	return nil, nil
}

func (m *mockRepo) GetByIDs(ctx context.Context, ids []int64) (map[int64]*domain.Job, error) {
	return map[int64]*domain.Job{}, nil
}

func (m *mockRepo) ExistsByURL(ctx context.Context, url string) (bool, error) {
	return m.exists[url], nil
}

func (m *mockRepo) TouchSeenAt(ctx context.Context, url string) error {
	m.touchedURLs = append(m.touchedURLs, url)
	return nil
}

func (m *mockRepo) TouchSeenAtByID(ctx context.Context, id int64) error {
	return nil
}

func (m *mockRepo) ExpireStaleJobs(ctx context.Context, olderThanDays int) ([]int64, error) {
	return nil, nil
}

func (m *mockRepo) ExpireByURL(ctx context.Context, url string) ([]int64, error) {
	m.expiredURLs = append(m.expiredURLs, url)
	if ids, ok := m.expiredIDsByURL[url]; ok {
		return ids, nil
	}
	return nil, nil
}

func (m *mockRepo) GetUnembeddedIDs(ctx context.Context, limit int) ([]int64, error) {
	return nil, nil
}

type mockDispatchRepo struct {
	saveAndStageFunc func(ctx context.Context, job *domain.Job, trace port.QueueDispatchTrace) (int64, bool, error)
	stageJobFunc     func(ctx context.Context, jobID int64, trace port.QueueDispatchTrace) error
}

func (m *mockDispatchRepo) SaveAndStageJobForEmbedding(ctx context.Context, job *domain.Job, trace port.QueueDispatchTrace) (int64, bool, error) {
	if m.saveAndStageFunc != nil {
		return m.saveAndStageFunc(ctx, job, trace)
	}
	return 0, false, nil
}

func (m *mockDispatchRepo) StageJobForEmbedding(ctx context.Context, jobID int64, trace port.QueueDispatchTrace) error {
	if m.stageJobFunc != nil {
		return m.stageJobFunc(ctx, jobID, trace)
	}
	return nil
}

func (m *mockDispatchRepo) ClaimPendingJobEmbeds(ctx context.Context, limit int, lease time.Duration) ([]port.PendingJobEmbed, error) {
	return nil, nil
}

func (m *mockDispatchRepo) DeletePendingJobEmbeds(ctx context.Context, jobIDs []int64) error {
	return nil
}

func (m *mockDispatchRepo) ReleasePendingJobEmbeds(ctx context.Context, jobIDs []int64) error {
	return nil
}

type mockCrawlNotifRepo struct {
	cancelledJobIDs []int64
	cancelCalled    bool
	cancelErr       error
}

func (m *mockCrawlNotifRepo) EnsurePending(context.Context, int64, int64, float64, float64, string, []string, string) (bool, bool, error) {
	return false, false, nil
}

func (m *mockCrawlNotifRepo) MarkDispatched(context.Context, int64, int64) error { return nil }

func (m *mockCrawlNotifRepo) MarkSent(context.Context, int64, int64) error { return nil }

func (m *mockCrawlNotifRepo) MarkFailed(context.Context, int64, int64) error { return nil }

func (m *mockCrawlNotifRepo) Delete(context.Context, int64, int64) error { return nil }

func (m *mockCrawlNotifRepo) SentRecently(context.Context, int64, time.Duration) (bool, error) {
	return false, nil
}

func (m *mockCrawlNotifRepo) CountToday(context.Context, int64) (int, error) { return 0, nil }
func (m *mockCrawlNotifRepo) CountMissedToday(context.Context, int64) (int, error) {
	return 0, nil
}

func (m *mockCrawlNotifRepo) GetPendingForUser(context.Context, int64) ([]port.PendingNotification, error) {
	return nil, nil
}
func (m *mockCrawlNotifRepo) ClaimPendingDigestNotifications(context.Context, int64, int) ([]port.PendingNotification, error) {
	return nil, nil
}
func (m *mockCrawlNotifRepo) ReleasePendingDigestNotifications(context.Context, int64, []int64) error {
	return nil
}
func (m *mockCrawlNotifRepo) ReclaimStaleDigestClaims(context.Context, time.Duration) (int64, error) {
	return 0, nil
}
func (m *mockCrawlNotifRepo) MarkMissed(context.Context, int64, int64) error { return nil }
func (m *mockCrawlNotifRepo) GetMissedForUser(context.Context, int64) ([]port.MissedNotification, error) {
	return nil, nil
}
func (m *mockCrawlNotifRepo) ConvertMissedToPending(context.Context, []int64) (int64, error) {
	return 0, nil
}
func (m *mockCrawlNotifRepo) DeleteNotifications(context.Context, []int64) (int64, error) {
	return 0, nil
}

func (m *mockCrawlNotifRepo) GetFreeUsersWithPendingNotifications(context.Context) ([]int64, error) {
	return nil, nil
}

func (m *mockCrawlNotifRepo) CancelPendingByJobIDs(_ context.Context, jobIDs []int64) (int64, error) {
	m.cancelCalled = true
	m.cancelledJobIDs = append(m.cancelledJobIDs, jobIDs...)
	if m.cancelErr != nil {
		return 0, m.cancelErr
	}
	return int64(len(jobIDs)), nil
}

func TestCrawlProjects_Execute_Success(t *testing.T) {
	ext := &mockExtractor{
		listURLs: []string{"https://kwork.ru/projects/1/view", "https://kwork.ru/projects/2/view"},
		detail:   &domain.Job{Title: "Test Job", Source: "kwork", RawHTML: "<html>detail</html>"},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		details:  map[string][]byte{"https://kwork.ru/projects/1/view": []byte("<html>1</html>")},
	}
	repo := &mockRepo{
		exists: map[string]bool{"https://kwork.ru/projects/2/view": true},
		saveID: 42,
	}
	stager := &mockDispatchRepo{
		saveAndStageFunc: func(ctx context.Context, job *domain.Job, trace port.QueueDispatchTrace) (int64, bool, error) {
			return 42, true, nil
		},
	}

	uc := NewCrawlProjects(fetcher, ext, repo, stager)
	saved, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 1 {
		t.Errorf("want saved=1, got %d", saved)
	}
	// Existing URL должен быть помечен через TouchSeenAt, а не пропущен молча.
	if len(repo.touchedURLs) != 1 || repo.touchedURLs[0] != "https://kwork.ru/projects/2/view" {
		t.Errorf("want TouchSeenAt called for existing URL, got %v", repo.touchedURLs)
	}
}

func TestCrawlProjects_Execute_TgchannelExistingJob_SkipsDetailFetchAndTouchesSeenAt(t *testing.T) {
	existingURL := "https://t.me/freelance_ru/1001?embed=1&mode=tme"
	ext := tgchannel.NewExtractor("freelance_ru")
	fetcher := &mockFetcher{
		listHTML: []byte(`<html><body><a class="tgme_widget_message_date" href="https://t.me/freelance_ru/1001">post</a></body></html>`),
		failURLs: map[string]error{
			existingURL: errors.New("detail fetch should be skipped for existing tgchannel jobs"),
		},
	}
	repo := &mockRepo{
		exists: map[string]bool{existingURL: true},
	}

	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	saved, err := uc.Execute(context.Background(), "https://t.me/s/freelance_ru")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 0 {
		t.Fatalf("want saved=0, got %d", saved)
	}
	if len(fetcher.calls) != 1 || fetcher.calls[0] != "https://t.me/s/freelance_ru" {
		t.Fatalf("want only list fetch, got %v", fetcher.calls)
	}
	if len(repo.touchedURLs) != 1 || repo.touchedURLs[0] != existingURL {
		t.Fatalf("want TouchSeenAt called for %q, got %v", existingURL, repo.touchedURLs)
	}
}

func TestCrawlProjects_Execute_FetchListFails(t *testing.T) {
	fetcher := &mockFetcher{listErr: errors.New("network error")}
	ext := &mockExtractor{listURLs: []string{}}
	repo := &mockRepo{}
	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	_, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err == nil {
		t.Fatal("want error on fetch list fail")
	}
}

func TestCrawlProjects_Execute_ExtractListFails(t *testing.T) {
	fetcher := &mockFetcher{listHTML: []byte("<html></html>")}
	ext := &mockExtractor{listErr: errors.New("parse error")}
	repo := &mockRepo{}
	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	_, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err == nil {
		t.Fatal("want error on extract list fail")
	}
}

func TestCrawlProjects_Execute_EmptyList(t *testing.T) {
	fetcher := &mockFetcher{listHTML: []byte("<html></html>")}
	ext := &mockExtractor{listURLs: nil}
	repo := &mockRepo{}
	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	saved, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 0 {
		t.Errorf("want saved=0, got %d", saved)
	}
}

func TestCrawlProjects_Execute_TouchSeenAt_OnExisting(t *testing.T) {
	existingURL := "https://kwork.ru/projects/2/view"
	newURL := "https://kwork.ru/projects/1/view"
	ext := &mockExtractor{
		listURLs: []string{newURL, existingURL},
		detail:   &domain.Job{Title: "Job", Source: "kwork"},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		details:  map[string][]byte{newURL: []byte("<html>1</html>")},
	}
	repo := &mockRepo{
		exists: map[string]bool{existingURL: true},
		saveID: 1,
	}
	stager := &mockDispatchRepo{
		saveAndStageFunc: func(_ context.Context, _ *domain.Job, _ port.QueueDispatchTrace) (int64, bool, error) {
			return 1, true, nil
		},
	}

	uc := NewCrawlProjects(fetcher, ext, repo, stager)
	_, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if len(repo.touchedURLs) != 1 || repo.touchedURLs[0] != existingURL {
		t.Errorf("want TouchSeenAt called for %q, got %v", existingURL, repo.touchedURLs)
	}
}

func TestCrawlProjects_Execute_ExistingJob_ExpiresWhenDetailFetchGone(t *testing.T) {
	url := "https://kwork.ru/projects/404/view"
	ext := &mockExtractor{
		listURLs: []string{url},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		failURLs: map[string]error{
			url: &domain.HttpStatusError{StatusCode: 404, URL: url},
		},
	}
	repo := &mockRepo{
		exists:          map[string]bool{url: true},
		expiredIDsByURL: map[string][]int64{url: {404}},
	}

	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	saved, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 0 {
		t.Fatalf("want saved=0, got %d", saved)
	}
	// New behaviour: 404 for an existing job must expire it, not touch it.
	if len(repo.expiredURLs) != 1 || repo.expiredURLs[0] != url {
		t.Fatalf("want ExpireByURL called for %q, got %v", url, repo.expiredURLs)
	}
	if len(repo.touchedURLs) != 0 {
		t.Fatalf("want TouchSeenAt NOT called, got %v", repo.touchedURLs)
	}
}

func TestCrawlProjects_Execute_ExistingJob_ExpiresWhenDetailFetch410(t *testing.T) {
	url := "https://kwork.ru/projects/410-existing/view"
	ext := &mockExtractor{
		listURLs: []string{url},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		failURLs: map[string]error{
			url: &domain.HttpStatusError{StatusCode: 410, URL: url},
		},
	}
	repo := &mockRepo{
		exists:          map[string]bool{url: true},
		expiredIDsByURL: map[string][]int64{url: {410}},
	}

	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	saved, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 0 {
		t.Fatalf("want saved=0, got %d", saved)
	}
	if len(repo.expiredURLs) != 1 || repo.expiredURLs[0] != url {
		t.Fatalf("want ExpireByURL called for %q, got %v", url, repo.expiredURLs)
	}
	if len(repo.touchedURLs) != 0 {
		t.Fatalf("want TouchSeenAt NOT called, got %v", repo.touchedURLs)
	}
}

func TestCrawlProjects_Execute_ExistingJob_TouchSeenAtWhenFetchOK(t *testing.T) {
	url := "https://kwork.ru/projects/existing-ok/view"
	ext := &mockExtractor{
		listURLs: []string{url},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		details:  map[string][]byte{url: []byte("<html>detail</html>")},
	}
	repo := &mockRepo{
		exists: map[string]bool{url: true},
	}

	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	saved, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 0 {
		t.Fatalf("want saved=0, got %d", saved)
	}
	// Existing job with successful fetch → TouchSeenAt called, NOT expired.
	if len(repo.touchedURLs) != 1 || repo.touchedURLs[0] != url {
		t.Fatalf("want TouchSeenAt called for %q, got %v", url, repo.touchedURLs)
	}
	if len(repo.expiredURLs) != 0 {
		t.Fatalf("want no ExpireByURL, got %v", repo.expiredURLs)
	}
}

func TestCrawlProjects_Execute_ReturnsCtxErr_WhenDetailFetchIsCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	url := "https://kwork.ru/projects/cancelled/view"
	ext := &mockExtractor{
		listURLs: []string{url},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		failURLs: map[string]error{
			url: ctx.Err(),
		},
	}
	repo := &mockRepo{}

	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	_, err := uc.Execute(ctx, "https://kwork.ru/projects")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context.Canceled, got %v", err)
	}
}

func TestCrawlProjects_Execute_NewJob_ExpiresWhenDetailFetchGone(t *testing.T) {
	url := "https://kwork.ru/projects/410/view"
	ext := &mockExtractor{
		listURLs: []string{url},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		failURLs: map[string]error{
			url: &domain.HttpStatusError{StatusCode: 410, URL: url},
		},
	}
	repo := &mockRepo{
		expiredIDsByURL: map[string][]int64{
			url: {410},
		},
	}
	notifRepo := &mockCrawlNotifRepo{}

	uc := NewCrawlProjects(fetcher, ext, repo, nil, notifRepo)
	saved, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 0 {
		t.Fatalf("want saved=0, got %d", saved)
	}
	if len(repo.expiredURLs) != 1 || repo.expiredURLs[0] != url {
		t.Fatalf("want ExpireByURL called for %q, got %v", url, repo.expiredURLs)
	}
	if len(repo.touchedURLs) != 0 {
		t.Fatalf("want no TouchSeenAt for new job, got %v", repo.touchedURLs)
	}
	if notifRepo.cancelCalled {
		t.Fatalf("want no separate CancelPendingByJobIDs call, got %v", notifRepo.cancelledJobIDs)
	}
}

func TestCrawlProjects_Execute_WithNilStager(t *testing.T) {
	ext := &mockExtractor{
		listURLs: []string{"https://kwork.ru/projects/1/view"},
		detail:   &domain.Job{Title: "Job", Source: "kwork"},
	}
	fetcher := &mockFetcher{
		listHTML: []byte("<html>list</html>"),
		details:  map[string][]byte{"https://kwork.ru/projects/1/view": []byte("<html>1</html>")},
	}
	repo := &mockRepo{saveID: 1}

	uc := NewCrawlProjects(fetcher, ext, repo, nil)
	saved, err := uc.Execute(context.Background(), "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if saved != 0 {
		t.Errorf("want saved=0, got %d", saved)
	}
}
