package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	redisclient "github.com/redis/go-redis/v9"
	"github.com/robfig/cron/v3"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/http"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/kwork"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisqueue "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/security"
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
		cronSpec = "*/5 * * * *" // каждые 5 мин по умолчанию
	}

	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dbURL)
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

	fetcher := http.NewFetcher(http.Config{
		Timeout:   30 * time.Second,
		RateLimit: rateLimit,
	})
	extractor := kwork.NewExtractor()
	repo := postgres.NewJobRepository(pool)
	crawl := usecase.NewCrawlProjects(fetcher, extractor, repo, queue)

	runCrawl := func() {
		crawlCtx := context.Background()
		saved, err := crawl.Execute(crawlCtx, listURL)
		if err != nil {
			slog.Error("crawl failed", "err", err)
			return
		}
		slog.Info("CrawlOnce done", "saved", saved)
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
	drainCtx := c.Stop()
	<-drainCtx.Done()
	slog.Info("shutdown complete")
}
