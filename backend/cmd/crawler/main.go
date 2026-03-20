package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	stdhttp "net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	redisclient "github.com/redis/go-redis/v9"
	"github.com/robfig/cron/v3"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/browser"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/flru"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/freelancehunt"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/http"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/kwork"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisqueue "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/weblancer"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/security"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/usecase"
	"go.opentelemetry.io/otel"
)

const jobEmbedDispatchFlushPeriod = 2 * time.Second

func main() {
	_ = godotenv.Load()
	_ = godotenv.Load("../.env") // при запуске из backend/

	isProd := security.IsProductionEnv(os.Getenv("APP_ENV"))

	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		slog.Error("DATABASE_URL not set")
		os.Exit(1)
	}
	if err := security.ValidateURLPassword("DATABASE_URL", dbURL, 16); err != nil {
		slog.Error("invalid DATABASE_URL secret policy", "err", err)
		os.Exit(1)
	}
	if isProd {
		if err := security.ValidatePostgresTLSForProduction("DATABASE_URL", dbURL); err != nil {
			slog.Error("invalid DATABASE_URL transport policy", "err", err)
			os.Exit(1)
		}
	}
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		if isProd {
			slog.Error("REDIS_URL not set")
			os.Exit(1)
		}
		redisURL = "redis://localhost:6379/0"
	}
	if isProd {
		if err := security.ValidateRedisTLSForProduction("REDIS_URL", redisURL, 16); err != nil {
			slog.Error("invalid REDIS_URL transport policy", "err", err)
			os.Exit(1)
		}
	}

	breakerFailureThreshold, err := parsePositiveIntEnv("CRAWL_BREAKER_FAILURE_THRESHOLD", 3)
	if err != nil {
		slog.Error("invalid CRAWL_BREAKER_FAILURE_THRESHOLD", "err", err)
		os.Exit(1)
	}
	breakerOpenInterval, err := parsePositiveDurationEnv("CRAWL_BREAKER_OPEN_INTERVAL", time.Minute)
	if err != nil {
		slog.Error("invalid CRAWL_BREAKER_OPEN_INTERVAL", "err", err)
		os.Exit(1)
	}
	retryMaxAttempts, err := parsePositiveIntEnv("CRAWL_RETRY_MAX_ATTEMPTS", 3)
	if err != nil {
		slog.Error("invalid CRAWL_RETRY_MAX_ATTEMPTS", "err", err)
		os.Exit(1)
	}
	retryBaseBackoff, err := parsePositiveDurationEnv("CRAWL_RETRY_BASE_BACKOFF", time.Second)
	if err != nil {
		slog.Error("invalid CRAWL_RETRY_BASE_BACKOFF", "err", err)
		os.Exit(1)
	}
	retryMaxBackoff, err := parsePositiveDurationEnv("CRAWL_RETRY_MAX_BACKOFF", 30*time.Second)
	if err != nil {
		slog.Error("invalid CRAWL_RETRY_MAX_BACKOFF", "err", err)
		os.Exit(1)
	}
	crawlRunTimeout, err := parsePositiveDurationEnv("CRAWL_RUN_TIMEOUT", 10*time.Minute)
	if err != nil {
		slog.Error("invalid CRAWL_RUN_TIMEOUT", "err", err)
		os.Exit(1)
	}
	if retryMaxBackoff < retryBaseBackoff {
		slog.Error("invalid retry backoff config: CRAWL_RETRY_MAX_BACKOFF must be >= CRAWL_RETRY_BASE_BACKOFF")
		os.Exit(1)
	}
	rateSec, err := parsePositiveIntEnv("CRAWL_RATE_SEC", 15)
	if err != nil {
		slog.Error("invalid CRAWL_RATE_SEC", "err", err)
		os.Exit(1)
	}
	rateLimit := time.Duration(rateSec) * time.Second

	// ENABLED_SOURCES — comma-separated список источников: kwork,flru,freelancehunt,weblancer
	// По умолчанию только kwork (обратная совместимость).
	enabledSourcesRaw := strings.TrimSpace(os.Getenv("ENABLED_SOURCES"))
	if enabledSourcesRaw == "" {
		enabledSourcesRaw = "kwork"
	}
	enabledSources := make(map[string]bool)
	for _, s := range strings.Split(enabledSourcesRaw, ",") {
		s = strings.TrimSpace(strings.ToLower(s))
		if s != "" {
			enabledSources[s] = true
		}
	}

	// Kwork list URL (поддерживаем CRAWL_LIST_URL для обратной совместимости)
	kworkListURL := os.Getenv("CRAWL_LIST_URL")
	if kworkListURL == "" {
		base := strings.TrimSuffix(os.Getenv("KWORK_BASE_URL"), "/")
		if base == "" {
			base = "https://kwork.ru"
		}
		kworkListURL = base + "/projects"
	}

	flruListURL := os.Getenv("FLRU_LIST_URL")
	if flruListURL == "" {
		flruListURL = "https://www.fl.ru/projects/"
	}

	freelancehuntListURL := os.Getenv("FREELANCEHUNT_LIST_URL")
	if freelancehuntListURL == "" {
		freelancehuntListURL = "https://freelancehunt.com/projects/"
	}

	weblancerListURL := os.Getenv("WEBLANCER_LIST_URL")
	if weblancerListURL == "" {
		weblancerListURL = "https://www.weblancer.net/jobs/"
	}

	proxyURL := strings.TrimSpace(os.Getenv("CRAWL_PROXY_URL"))
	if proxyURL != "" {
		if validateErr := security.ValidateHTTPOrHTTPSURL("CRAWL_PROXY_URL", proxyURL); validateErr != nil {
			slog.Error("invalid CRAWL_PROXY_URL", "err", validateErr)
			os.Exit(1)
		}
	}
	if proxyURL != "" {
		slog.Info("crawler proxy configured", "proxy", proxyURL)
	}

	jobExpireDays, err := parsePositiveIntEnv("JOB_EXPIRE_DAYS", 14)
	if err != nil {
		slog.Error("invalid JOB_EXPIRE_DAYS", "err", err)
		os.Exit(1)
	}
	jobExpireCheckSec, err := parsePositiveIntEnv("JOB_EXPIRE_CHECK_SEC", 3600)
	if err != nil {
		slog.Error("invalid JOB_EXPIRE_CHECK_SEC", "err", err)
		os.Exit(1)
	}

	cronSpec := os.Getenv("CRAWL_CRON")
	if cronSpec == "" {
		cronSpec = "0 5 * * *" // каждый день в 05:00 UTC по умолчанию
	}

	ctx := context.Background()
	shutdownTracer, err := telemetry.InitTracerProvider(ctx, "site-parser-crawler", os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
	if err != nil {
		slog.Warn("tracer init failed, tracing disabled", "err", err)
	} else {
		defer func() { _ = shutdownTracer(context.Background()) }()
	}

	pool, err := postgres.NewConfiguredPool(ctx, dbURL)
	if err != nil {
		slog.Error("pgxpool", "err", err)
		os.Exit(1)
	}
	defer pool.Close()

	redisOpt, err := redisclient.ParseURL(redisURL)
	if err != nil {
		slog.Error("redis parse url", "err", err)
		os.Exit(1)
	}
	rdb := redisclient.NewClient(redisOpt)
	defer func() { _ = rdb.Close() }()
	if pingErr := rdb.Ping(ctx).Err(); pingErr != nil {
		slog.Error("redis ping", "err", pingErr)
		os.Exit(1)
	}

	queueName := os.Getenv("AI_QUEUE")
	if queueName == "" {
		queueName = "ai-process"
	}
	queue := redisqueue.NewQueue(rdb, queueName)
	dispatchRepo := postgres.NewDispatchRepository(pool)
	jobEmbedDispatcher := usecase.NewPendingJobEmbedDispatcher(dispatchRepo, queue, 30*time.Second)
	registry := prometheus.NewRegistry()
	registry.MustRegister(
		collectors.NewGoCollector(),
		collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}),
	)
	crawlerMetrics := telemetry.NewCrawlerMetrics(registry, queueName)

	fetcher := http.NewFetcher(http.Config{
		Timeout:                 30 * time.Second,
		RateLimit:               rateLimit,
		BreakerFailureThreshold: breakerFailureThreshold,
		BreakerOpenInterval:     breakerOpenInterval,
		RetryMaxAttempts:        retryMaxAttempts,
		RetryBaseBackoff:        retryBaseBackoff,
		RetryMaxBackoff:         retryMaxBackoff,
		ProxyURL:                proxyURL,
	})
	repo := postgres.NewJobRepository(pool)

	// BROWSER_SERVICE_URL — адрес Browser Render Service (browser-service).
	// Если задан, Kwork использует headless-браузер для рендера JS-страниц.
	// Если не задан — используется обычный HTTP-фетчер (без JS-рендера).
	browserServiceURL := strings.TrimSpace(os.Getenv("BROWSER_SERVICE_URL"))
	if browserServiceURL != "" {
		if validateErr := security.ValidateHTTPOrHTTPSURL("BROWSER_SERVICE_URL", browserServiceURL); validateErr != nil {
			slog.Error("invalid BROWSER_SERVICE_URL", "err", validateErr)
			os.Exit(1)
		}
	}
	var kworkFetcher port.Fetcher = fetcher
	var browserHealth crawlerHTTPPinger
	if browserServiceURL != "" && enabledSources["kwork"] {
		browserFetcher := browser.NewFetcher(browserServiceURL)
		kworkFetcher = browserFetcher
		browserHealth = browserFetcher
		slog.Info("kwork using browser render service", "service", browserServiceURL)
	} else if enabledSources["kwork"] {
		slog.Warn("BROWSER_SERVICE_URL not set: kwork list will use plain HTTP fetcher (JS-rendered content won't be visible)")
	}

	type crawlSource struct {
		name    string
		listURL string
		crawl   *usecase.CrawlProjects
	}
	var sources []crawlSource
	if enabledSources["kwork"] {
		sources = append(sources, crawlSource{
			name:    "kwork",
			listURL: kworkListURL,
			crawl:   usecase.NewCrawlProjects(kworkFetcher, kwork.NewExtractor(), repo, dispatchRepo),
		})
	}
	if enabledSources["flru"] {
		sources = append(sources, crawlSource{
			name:    "flru",
			listURL: flruListURL,
			crawl:   usecase.NewCrawlProjects(fetcher, flru.NewExtractor(), repo, dispatchRepo),
		})
	}
	if enabledSources["freelancehunt"] {
		sources = append(sources, crawlSource{
			name:    "freelancehunt",
			listURL: freelancehuntListURL,
			crawl:   usecase.NewCrawlProjects(fetcher, freelancehunt.NewExtractor(), repo, dispatchRepo),
		})
	}
	if enabledSources["weblancer"] {
		sources = append(sources, crawlSource{
			name:    "weblancer",
			listURL: weblancerListURL,
			crawl:   usecase.NewCrawlProjects(fetcher, weblancer.NewExtractor(), repo, dispatchRepo),
		})
	}
	if len(sources) == 0 {
		slog.Error("no valid sources configured in ENABLED_SOURCES", "raw", enabledSourcesRaw)
		os.Exit(1)
	}
	slog.Info("crawler sources configured", "sources", enabledSourcesRaw)

	expireJobs := usecase.NewExpireJobs(repo, jobExpireDays)

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	go runJobEmbedDispatchLoop(ctx, jobEmbedDispatcher)
	go runExpireJobsLoop(ctx, expireJobs, time.Duration(jobExpireCheckSec)*time.Second)

	healthAddr := os.Getenv("CRAWLER_HEALTH_ADDR")
	if healthAddr == "" {
		healthAddr = ":8081"
	}
	healthMux := stdhttp.NewServeMux()
	healthMux.HandleFunc("/healthz", crawlerHealthz())
	healthMux.HandleFunc("/readyz", crawlerReadyz(pool, crawlerRedisClientPinger{client: rdb}, browserHealth))
	healthMux.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	healthSrv := &stdhttp.Server{
		Addr:         healthAddr,
		Handler:      healthMux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
	}
	go func() {
		slog.Info("crawler health server listening", "addr", healthAddr)
		if serveErr := healthSrv.ListenAndServe(); serveErr != nil && serveErr != stdhttp.ErrServerClosed {
			slog.Error("crawler health server failed", "err", serveErr)
		}
	}()
	go func() {
		<-ctx.Done()
		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutdownCancel()
		_ = healthSrv.Shutdown(shutdownCtx)
	}()

	runCrawl := func() {
		startedAt := time.Now()
		defer observeCrawlerQueueDepth(rdb, crawlerMetrics, queueName)
		traceID := observability.NewTraceID()
		crawlCtx := observability.WithTraceID(ctx, traceID)
		crawlCtx, span := otel.Tracer("site-parser-crawler").Start(crawlCtx, "crawl.run")
		defer span.End()
		crawlCtx, cancelCrawl := context.WithTimeout(crawlCtx, crawlRunTimeout)
		defer cancelCrawl()

		totalSaved := 0
		for _, src := range sources {
			if crawlCtx.Err() != nil {
				break
			}
			slog.Info("crawl run started", "source", src.name, "url", src.listURL, "trace_id", traceID)
			saved, crawlErr := src.crawl.Execute(crawlCtx, src.listURL)
			if crawlErr != nil {
				if errors.Is(crawlCtx.Err(), context.Canceled) {
					crawlerMetrics.ObserveRunInterrupted(time.Since(startedAt))
					slog.Info("crawl interrupted by shutdown", "source", src.name)
					return
				}
				if errors.Is(crawlCtx.Err(), context.DeadlineExceeded) {
					crawlerMetrics.ObserveRunFailure(time.Since(startedAt))
					slog.Error("crawl timed out", "source", src.name, "timeout", crawlRunTimeout, "trace_id", traceID)
					return
				}
				crawlerMetrics.ObserveRunFailure(time.Since(startedAt))
				slog.Error("crawl failed", "source", src.name, "err", crawlErr, "trace_id", traceID)
				continue
			}
			slog.Info("crawl source done", "source", src.name, "saved", saved, "trace_id", traceID)
			totalSaved += saved
		}
		crawlerMetrics.ObserveRunSuccess(totalSaved, time.Since(startedAt))
		slog.Info("CrawlOnce done", "total_saved", totalSaved, "sources", len(sources), "trace_id", traceID)

		// Re-enqueue any jobs that were saved but never reached the ai-process queue
		// (e.g. due to a Redis blip during a previous crawl run).
		if requeued, requeueErr := sources[0].crawl.RequeueOrphaned(crawlCtx, 50); requeueErr != nil {
			slog.Warn("crawl: orphan requeue scan failed", "err", requeueErr)
		} else if requeued > 0 {
			slog.Info("crawl: orphaned jobs requeued", "count", requeued, "trace_id", traceID)
		}
		if flushed, flushErr := jobEmbedDispatcher.Flush(crawlCtx, 500); flushErr != nil {
			slog.Warn("crawl: pending job dispatch flush failed", "err", flushErr, "trace_id", traceID)
		} else if flushed > 0 {
			slog.Info("crawl: pending jobs dispatched", "count", flushed, "trace_id", traceID)
		}
	}

	c := cron.New()
	_, err = c.AddFunc(cronSpec, runCrawl)
	if err != nil {
		slog.Error("cron add func", "spec", cronSpec, "err", err)
		os.Exit(1)
	}
	c.Start()

	// Первый запуск сразу
	runCrawl()

	slog.Info("crawler started", "cron", cronSpec)
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	signal.Stop(sigCh)
	slog.Info("shutdown signal received, stopping")
	cancel()
	drainCtx := c.Stop()
	<-drainCtx.Done()
	slog.Info("shutdown complete")
}

type crawlerDBPinger interface {
	Ping(ctx context.Context) error
}

type crawlerRedisPinger interface {
	Ping(ctx context.Context) error
}

type crawlerHTTPPinger interface {
	Ping(ctx context.Context) error
}

type crawlerRedisClientPinger struct {
	client *redisclient.Client
}

func (p crawlerRedisClientPinger) Ping(ctx context.Context) error {
	if p.client == nil {
		return fmt.Errorf("redis client is not configured")
	}
	return p.client.Ping(ctx).Err()
}

func crawlerHealthz() stdhttp.HandlerFunc {
	return func(w stdhttp.ResponseWriter, r *stdhttp.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(stdhttp.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}
}

func crawlerReadyz(db crawlerDBPinger, redis crawlerRedisPinger, browser crawlerHTTPPinger) stdhttp.HandlerFunc {
	return func(w stdhttp.ResponseWriter, r *stdhttp.Request) {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()

		if db == nil {
			stdhttp.Error(w, "db not ready", stdhttp.StatusServiceUnavailable)
			return
		}
		if err := db.Ping(ctx); err != nil {
			stdhttp.Error(w, "db not ready", stdhttp.StatusServiceUnavailable)
			return
		}
		if redis == nil {
			stdhttp.Error(w, "redis not ready", stdhttp.StatusServiceUnavailable)
			return
		}
		if err := redis.Ping(ctx); err != nil {
			stdhttp.Error(w, "redis not ready", stdhttp.StatusServiceUnavailable)
			return
		}
		if browser != nil {
			if err := browser.Ping(ctx); err != nil {
				stdhttp.Error(w, "browser service not ready", stdhttp.StatusServiceUnavailable)
				return
			}
		}

		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(stdhttp.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}
}

func parsePositiveIntEnv(key string, fallback int) (int, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("%s must be positive integer: %w", key, err)
	}
	if v <= 0 {
		return 0, fmt.Errorf("%s must be > 0", key)
	}
	return v, nil
}

func parsePositiveDurationEnv(key string, fallback time.Duration) (time.Duration, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := time.ParseDuration(raw)
	if err != nil {
		return 0, fmt.Errorf("%s must be valid duration: %w", key, err)
	}
	if v <= 0 {
		return 0, fmt.Errorf("%s must be > 0", key)
	}
	return v, nil
}

func observeCrawlerQueueDepth(client *redisclient.Client, metrics *telemetry.CrawlerMetrics, queueName string) {
	if client == nil || metrics == nil || queueName == "" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	ready, err := client.LLen(ctx, queueName).Result()
	if err != nil {
		slog.Warn("crawler queue depth collect failed", "queue", queueName, "err", err)
		return
	}
	processing, err := client.LLen(ctx, queueName+":processing").Result()
	if err != nil {
		slog.Warn("crawler queue depth collect failed", "queue", queueName+":processing", "err", err)
		return
	}
	dlq, err := client.LLen(ctx, queueName+":dlq").Result()
	if err != nil {
		slog.Warn("crawler queue depth collect failed", "queue", queueName+":dlq", "err", err)
		return
	}
	metrics.SetQueueDepth(ready, processing, dlq)
}

func runExpireJobsLoop(ctx context.Context, uc *usecase.ExpireJobs, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		if _, err := uc.Execute(ctx); err != nil && ctx.Err() == nil {
			slog.Warn("expire_jobs: failed", "err", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func runJobEmbedDispatchLoop(ctx context.Context, dispatcher *usecase.PendingJobEmbedDispatcher) {
	if dispatcher == nil {
		return
	}
	ticker := time.NewTicker(jobEmbedDispatchFlushPeriod)
	defer ticker.Stop()
	for {
		if _, err := dispatcher.Flush(ctx, 200); err != nil && ctx.Err() == nil {
			slog.Warn("job-embed dispatch flush failed", "err", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
