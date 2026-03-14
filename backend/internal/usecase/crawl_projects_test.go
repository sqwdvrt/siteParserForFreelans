package usecase

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockFetcher struct {
	listHTML []byte
	listErr  error
	details  map[string][]byte
	failURLs map[string]error
}

func (m *mockFetcher) Fetch(ctx context.Context, url string) ([]byte, error) {
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
	exists  map[string]bool
	saveID  int64
	saveErr error
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

func TestCrawlProjects_Execute_Success(t *testing.T) {
	ext := &mockExtractor{
		listURLs: []string{"https://kwork.ru/projects/1/view", "https://kwork.ru/projects/2/view"},
		detail:   &domain.Job{Title: "Test Job", Source: "kwork"},
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
