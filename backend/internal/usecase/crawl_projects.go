package usecase

import (
	"context"
	"log/slog"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// CrawlProjects — use case: fetch list → details → save → enqueue.
type CrawlProjects struct {
	fetcher   port.Fetcher
	extractor port.Extractor
	repo      port.JobRepository
	queue     port.JobQueue
}

// NewCrawlProjects создаёт use case.
func NewCrawlProjects(fetcher port.Fetcher, extractor port.Extractor, repo port.JobRepository, queue port.JobQueue) *CrawlProjects {
	return &CrawlProjects{
		fetcher:   fetcher,
		extractor: extractor,
		repo:      repo,
		queue:     queue,
	}
}

// Execute выполняет обход: загружает список, для каждого URL — детали, сохраняет.
// listURL — URL страницы со списком проектов (например, https://kwork.ru/projects).
// Возвращает количество сохранённых jobs.
func (u *CrawlProjects) Execute(ctx context.Context, listURL string) (saved int, err error) {
	html, err := u.fetcher.Fetch(ctx, listURL)
	if err != nil {
		return 0, err
	}
	urls, err := u.extractor.ExtractList(html)
	if err != nil {
		return 0, err
	}
	slog.Info("crawl: found projects", "count", len(urls), "url", listURL)
	for _, detailURL := range urls {
		exists, err := u.repo.ExistsByURL(ctx, detailURL)
		if err != nil {
			slog.Warn("crawl: check exists failed", "url", detailURL, "err", err)
			continue
		}
		if exists {
			continue
		}
		detailHTML, err := u.fetcher.Fetch(ctx, detailURL)
		if err != nil {
			slog.Warn("crawl: fetch detail failed", "url", detailURL, "err", err)
			continue
		}
		job, err := u.extractor.ExtractDetail(detailHTML, detailURL)
		if err != nil || job == nil {
			slog.Warn("crawl: extract detail failed", "url", detailURL, "err", err)
			continue
		}
		id, err := u.repo.Save(ctx, job)
		if err != nil {
			slog.Warn("crawl: save failed", "url", detailURL, "err", err)
			continue
		}
		if id > 0 && u.queue != nil {
			if err := u.queue.Enqueue(ctx, id); err != nil {
				slog.Warn("crawl: enqueue failed", "id", id, "err", err)
			}
		}
		saved++
		slog.Info("crawl: saved job", "id", id, "url", detailURL, "title", job.Title)
	}
	slog.Info("crawl: done", "saved", saved, "total_found", len(urls))
	return saved, nil
}
