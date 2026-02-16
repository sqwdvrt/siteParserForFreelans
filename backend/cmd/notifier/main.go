package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	redisclient "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisadapter "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/telegram"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/security"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/usecase"
)

const (
	popErrorBackoffMin = 500 * time.Millisecond
	popErrorBackoffMax = 30 * time.Second
)

func main() {
	_ = godotenv.Load()
	_ = godotenv.Load("../.env")

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

	token := os.Getenv("TELEGRAM_BOT_TOKEN")
	if token == "" {
		slog.Error("TELEGRAM_BOT_TOKEN not set")
		os.Exit(1)
	}
	if err := security.ValidateSecret("TELEGRAM_BOT_TOKEN", token, 20); err != nil {
		slog.Error("invalid TELEGRAM_BOT_TOKEN secret policy", "err", err)
		os.Exit(1)
	}

	queueName := os.Getenv("MATCH_NOTIFY_QUEUE")
	if queueName == "" {
		queueName = "match-notify"
	}

	rateSec, _ := strconv.Atoi(os.Getenv("NOTIFY_RATE_LIMIT_SEC"))
	rateLimit := time.Duration(rateSec) * time.Second
	if rateLimit <= 0 {
		rateLimit = 5 * time.Minute
	}

	maxPerDay, _ := strconv.Atoi(os.Getenv("NOTIFY_MAX_PER_DAY"))
	if maxPerDay <= 0 {
		maxPerDay = 5
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

	notifRepo := postgres.NewNotificationRepository(pool)
	userRepo := postgres.NewUserRepository(pool)
	jobRepo := postgres.NewJobRepository(pool)
	notifier := telegram.NewNotifier(token)
	sendNotif := usecase.NewSendNotification(notifRepo, userRepo, jobRepo, notifier, rateLimit, maxPerDay)

	consumer := redisadapter.NewMatchNotifyConsumer(rdb, queueName)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	go func() {
		<-sigCh
		signal.Stop(sigCh)
		slog.Info("shutdown signal received")
		cancel()
	}()

	slog.Info("notifier started", "queue", queueName)
	var popErrBackoff time.Duration
	for {
		select {
		case <-ctx.Done():
			slog.Info("notifier stopped")
			return
		default:
			p, err := consumer.Pop(ctx)
			if err != nil {
				popErrBackoff = nextPopErrorBackoff(popErrBackoff)
				slog.Error("pop failed", "err", err, "backoff", popErrBackoff)
				if !waitForBackoff(ctx, popErrBackoff) {
					slog.Info("notifier stopped")
					return
				}
				continue
			}
			popErrBackoff = 0 // reset after successful read path
			if p == nil {
				continue
			}
			if err := sendNotif.Execute(ctx, p.UserID, p.JobID, p.MatchScore); err != nil {
				slog.Error("send notification failed", "user_id", p.UserID, "job_id", p.JobID, "err", err)
			}
		}
	}
}

func nextPopErrorBackoff(current time.Duration) time.Duration {
	if current <= 0 {
		return popErrorBackoffMin
	}
	next := current * 2
	if next > popErrorBackoffMax {
		return popErrorBackoffMax
	}
	return next
}

func waitForBackoff(ctx context.Context, d time.Duration) bool {
	if d <= 0 {
		return true
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
