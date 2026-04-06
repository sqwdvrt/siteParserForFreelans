package usecase

import (
	"context"
	"log/slog"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// CrawlProjects — use case: fetch list → details → save → stage deferred enqueue.
type CrawlProjects struct {
	fetcher        port.Fetcher
	extractor      port.Extractor
	repo           port.JobRepository
	stager         port.JobEmbedDispatchRepository
	findSimilarJob FindSimilarJob // optional: cross-platform dedup
}

// FindSimilarJob checks for a cross-platform duplicate of the given job.
// Returns (canonicalJobID, similarity) if found, or (0, 0) if unique.
type FindSimilarJob func(ctx context.Context, title, description, budget, excludeSource string) (int64, float64, error)

// NewCrawlProjects создаёт use case.
func NewCrawlProjects(
	fetcher port.Fetcher,
	extractor port.Extractor,
	repo port.JobRepository,
	stager port.JobEmbedDispatchRepository,
	notifRepos ...port.NotificationRepository,
) *CrawlProjects {
	return &CrawlProjects{
		fetcher:   fetcher,
		extractor: extractor,
		repo:      repo,
		stager:    stager,
	}
}

// WithCrossPlatformDedup sets a function to detect duplicate jobs across sources.
func (u *CrawlProjects) WithCrossPlatformDedup(fn FindSimilarJob) *CrawlProjects {
	u.findSimilarJob = fn
	return u
}

type existingDetailFetchPolicy interface {
	FetchExistingDetails() bool
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
		if err := ctx.Err(); err != nil {
			return saved, err
		}

		exists, err := u.repo.ExistsByURL(ctx, detailURL)
		if err != nil {
			if ctxErr := ctx.Err(); ctxErr != nil {
				return saved, ctxErr
			}
			slog.Warn("crawl: check exists failed", "url", detailURL, "err", err)
			continue
		}

		if exists && !shouldFetchExistingDetails(u.extractor) {
			if touchErr := u.repo.TouchSeenAt(ctx, detailURL); touchErr != nil {
				if ctxErr := ctx.Err(); ctxErr != nil {
					return saved, ctxErr
				}
				slog.Warn("crawl: touch seen_at failed", "url", detailURL, "err", touchErr)
			}
			continue
		}

		// Fetch detail for ALL URLs — this is the only way to detect 404/410 for existing jobs too.
		detailHTML, err := u.fetcher.Fetch(ctx, detailURL)
		if err != nil {
			if ctxErr := ctx.Err(); ctxErr != nil {
				return saved, ctxErr
			}
			slog.Warn("crawl: fetch detail failed", "url", detailURL, "err", err)
			if domain.IsGone(err) {
				expiredIDs, expErr := u.repo.ExpireByURL(ctx, detailURL)
				if expErr != nil {
					slog.Warn("crawl: expire by url failed", "url", detailURL, "err", expErr)
					continue
				}
				if len(expiredIDs) > 0 {
					slog.Info("crawl: expired gone job", "count", len(expiredIDs), "url", detailURL)
				}
			}
			continue
		}

		if exists {
			if touchErr := u.repo.TouchSeenAt(ctx, detailURL); touchErr != nil {
				if ctxErr := ctx.Err(); ctxErr != nil {
					return saved, ctxErr
				}
				slog.Warn("crawl: touch seen_at failed", "url", detailURL, "err", touchErr)
			}
			continue
		}

		job, err := u.extractor.ExtractDetail(detailHTML, detailURL)
		if err != nil || job == nil {
			slog.Warn("crawl: extract detail failed", "url", detailURL, "err", err)
			continue
		}

		// Schema validation: reject invalid jobs before they hit the database
		if validationErr := ValidateJob(job); len(validationErr) > 0 {
			slog.Warn("crawl: job validation failed", "url", detailURL, "errors", validationErr)
			continue
		}

		// Cross-platform deduplication: check if similar job exists from another source
		if u.findSimilarJob != nil {
			dupID, similarity, dupErr := u.findSimilarJob(ctx, job.Title, job.Description, job.Budget, job.Source)
			if dupErr != nil {
				slog.Warn("crawl: duplicate check failed", "url", detailURL, "err", dupErr)
			} else if dupID > 0 {
				slog.Info("crawl: cross-platform duplicate detected, skipping",
					"url", detailURL,
					"canonical_id", dupID,
					"similarity", similarity,
					"source", job.Source,
				)
				// Update last_seen_at on the canonical job
				if touchErr := u.repo.TouchSeenAtByID(ctx, dupID); touchErr != nil {
					slog.Warn("crawl: touch canonical seen_at failed", "id", dupID, "err", touchErr)
				}
				continue
			}
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
			if ctxErr := ctx.Err(); ctxErr != nil {
				return saved, ctxErr
			}
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

func shouldFetchExistingDetails(extractor port.Extractor) bool {
	policy, ok := extractor.(existingDetailFetchPolicy)
	if !ok {
		return true
	}
	return policy.FetchExistingDetails()
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
