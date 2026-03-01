package main

import (
	"context"
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
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/http"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/kwork"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisqueue "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/security"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/usecase"
)

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
		redisURL = "redis://localhost:6379/0"
	}
	if isProd {
		if err := security.ValidateRedisTLSForProduction("REDIS_URL", redisURL, 16); err != nil {
			slog.Error("invalid REDIS_URL transport policy", "err", err)
			os.Exit(1)
		}
	}

	rateSec, _ := strconv.Atoi(os.Getenv("CRAWL_RATE_SEC"))
	if rateSec <= 0 {
		rateSec = 15
	}
	rateLimit := time.Duration(rateSec) * time.Second
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
	if retryMaxBackoff < retryBaseBackoff {
		slog.Error("invalid retry backoff config: CRAWL_RETRY_MAX_BACKOFF must be >= CRAWL_RETRY_BASE_BACKOFF")
		os.Exit(1)
	}

	listURL := os.Getenv("CRAWL_LIST_URL")
	if listURL == "" {
		base := strings.TrimSuffix(os.Getenv("KWORK_BASE_URL"), "/")
		if base == "" {
			base = "https://kwork.ru"
		}
		listURL = base + "/projects"
	}

	cronSpec := os.Getenv("CRAWL_CRON")
	if cronSpec == "" {
		cronSpec = "*/10 * * * *" // каждые 10 мин по умолчанию
	}

	ctx := context.Background()
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
	defer rdb.Close()
	if err := rdb.Ping(ctx).Err(); err != nil {
		slog.Error("redis ping", "err", err)
		os.Exit(1)
	}

	queueName := os.Getenv("AI_QUEUE")
	if queueName == "" {
		queueName = "ai-process"
	}
	queue := redisqueue.NewQueue(rdb, queueName)
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
	})
	extractor := kwork.NewExtractor()
	repo := postgres.NewJobRepository(pool)
	crawl := usecase.NewCrawlProjects(fetcher, extractor, repo, queue)

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	healthAddr := os.Getenv("CRAWLER_HEALTH_ADDR")
	if healthAddr == "" {
		healthAddr = ":8081"
	}
	healthMux := stdhttp.NewServeMux()
	healthMux.HandleFunc("/healthz", crawlerHealthz())
	healthMux.HandleFunc("/readyz", crawlerReadyz(pool, crawlerRedisClientPinger{client: rdb}))
	healthMux.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	healthSrv := &stdhttp.Server{
		Addr:         healthAddr,
		Handler:      healthMux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
	}
	go func() {
		slog.Info("crawler health server listening", "addr", healthAddr)
		if err := healthSrv.ListenAndServe(); err != nil && err != stdhttp.ErrServerClosed {
			slog.Error("crawler health server failed", "err", err)
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
		slog.Info("crawl run started", "url", listURL, "trace_id", traceID)
		saved, err := crawl.Execute(crawlCtx, listURL)
		if err != nil {
			if crawlCtx.Err() != nil {
				crawlerMetrics.ObserveRunInterrupted(time.Since(startedAt))
				slog.Info("crawl interrupted by shutdown")
				return
			}
			crawlerMetrics.ObserveRunFailure(time.Since(startedAt))
			slog.Error("crawl failed", "err", err, "trace_id", traceID)
			return
		}
		crawlerMetrics.ObserveRunSuccess(saved, time.Since(startedAt))
		slog.Info("CrawlOnce done", "saved", saved, "trace_id", traceID)
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

func crawlerReadyz(db crawlerDBPinger, redis crawlerRedisPinger) stdhttp.HandlerFunc {
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
