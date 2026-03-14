package usecase

import (
	"context"
	"log/slog"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// CrawlProjects — use case: fetch list → details → save → stage deferred enqueue.
type CrawlProjects struct {
	fetcher   port.Fetcher
	extractor port.Extractor
	repo      port.JobRepository
	stager    port.JobEmbedDispatchRepository
}

// NewCrawlProjects создаёт use case.
func NewCrawlProjects(
	fetcher port.Fetcher,
	extractor port.Extractor,
	repo port.JobRepository,
	stager port.JobEmbedDispatchRepository,
) *CrawlProjects {
	return &CrawlProjects{
		fetcher:   fetcher,
		extractor: extractor,
		repo:      repo,
		stager:    stager,
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
		if u.stager == nil {
			slog.Error("crawl: job dispatch stager is not configured", "url", detailURL)
			continue
		}
		id, inserted, err := u.stager.SaveAndStageJobForEmbedding(
			ctx,
			job,
			observability.QueueDispatchTraceFromContext(ctx),
		)
		if err != nil {
			slog.Warn("crawl: save failed", "url", detailURL, "err", err)
			continue
		}
		if !inserted {
			continue
		}
		saved++
		slog.Info("crawl: saved job", "id", id, "url", detailURL, "title", job.Title)
	}
	slog.Info("crawl: done", "saved", saved, "total_found", len(urls))
	return saved, nil
}

// RequeueOrphaned finds jobs with no embedding and stages them for deferred enqueue.
// Returns the number of jobs staged.
func (u *CrawlProjects) RequeueOrphaned(ctx context.Context, limit int) (int, error) {
	if u.stager == nil {
		return 0, nil
	}
	ids, err := u.repo.GetUnembeddedIDs(ctx, limit)
	if err != nil {
		return 0, err
	}
	requeued := 0
	for _, id := range ids {
		if err := u.stager.StageJobForEmbedding(ctx, id, port.QueueDispatchTrace{}); err != nil {
			slog.Warn("crawl: requeue orphaned failed", "id", id, "err", err)
		} else {
			requeued++
		}
	}
	if requeued > 0 {
		slog.Info("crawl: requeued orphaned jobs", "count", requeued)
	}
	return requeued, nil
}
