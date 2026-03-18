package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
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
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisadapter "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/telegram"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/security"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/usecase"
	"go.opentelemetry.io/otel"
)

const (
	popErrorBackoffMin    = 500 * time.Millisecond
	popErrorBackoffMax    = 30 * time.Second
	nackRecoverBackoffMin = 1 * time.Second
	nackRecoverBackoffMax = 30 * time.Second

	defaultNotifierMaxRetries      = 3
	defaultNotifierRetryBaseWait   = 1 * time.Second
	defaultBreakerFailureThreshold = 3
	defaultBreakerOpenInterval     = 30 * time.Second
	defaultBreakerOpenJitter       = 0.2
	defaultQueueDepthSamplePeriod  = 60 * time.Second
	localTelegramTokenEnv          = "LOCAL_TELEGRAM_BOT_TOKEN"
)

func resolveTelegramBotToken(isProd bool) (token string, source string) {
	if !isProd {
		if local := strings.TrimSpace(os.Getenv(localTelegramTokenEnv)); local != "" {
			return local, localTelegramTokenEnv
		}
	}
	return strings.TrimSpace(os.Getenv("TELEGRAM_BOT_TOKEN")), "TELEGRAM_BOT_TOKEN"
}

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

	token, tokenSource := resolveTelegramBotToken(isProd)
	if token == "" {
		slog.Error("telegram bot token not set", "checked", []string{localTelegramTokenEnv, "TELEGRAM_BOT_TOKEN"})
		os.Exit(1)
	}
	if err := security.ValidateSecret(tokenSource, token, 20); err != nil {
		slog.Error("invalid telegram bot token secret policy", "source", tokenSource, "err", err)
		os.Exit(1)
	}

	queueName := os.Getenv("MATCH_NOTIFY_QUEUE")
	if queueName == "" {
		queueName = "match-notify"
	}
	registry := prometheus.NewRegistry()
	registry.MustRegister(
		collectors.NewGoCollector(),
		collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}),
	)
	notifierMetrics := telemetry.NewNotifierMetrics(registry, queueName)

	rateSec, err := parsePositiveIntEnv("NOTIFY_RATE_LIMIT_SEC", 300)
	if err != nil {
		slog.Error("invalid NOTIFY_RATE_LIMIT_SEC", "err", err)
		os.Exit(1)
	}
	rateLimit := time.Duration(rateSec) * time.Second

	maxPerDay := getNotifierMaxPerDay()
	notifierMaxRetries, err := parsePositiveIntEnv("NOTIFIER_MAX_RETRIES", defaultNotifierMaxRetries)
	if err != nil {
		slog.Error("invalid NOTIFIER_MAX_RETRIES", "err", err)
		os.Exit(1)
	}
	notifierRetryBaseWait, err := parsePositiveDurationEnv("NOTIFIER_RETRY_BASE_WAIT", defaultNotifierRetryBaseWait)
	if err != nil {
		slog.Error("invalid NOTIFIER_RETRY_BASE_WAIT", "err", err)
		os.Exit(1)
	}
	breakerFailureThreshold, err := parsePositiveIntEnv("NOTIFIER_BREAKER_FAILURE_THRESHOLD", defaultBreakerFailureThreshold)
	if err != nil {
		slog.Error("invalid NOTIFIER_BREAKER_FAILURE_THRESHOLD", "err", err)
		os.Exit(1)
	}
	breakerOpenInterval, err := parsePositiveDurationEnv("NOTIFIER_BREAKER_OPEN_INTERVAL", defaultBreakerOpenInterval)
	if err != nil {
		slog.Error("invalid NOTIFIER_BREAKER_OPEN_INTERVAL", "err", err)
		os.Exit(1)
	}
	breakerOpenJitter, err := parseFloatEnvInRange("NOTIFIER_BREAKER_OPEN_JITTER", defaultBreakerOpenJitter, 0, 1)
	if err != nil {
		slog.Error("invalid NOTIFIER_BREAKER_OPEN_JITTER", "err", err)
		os.Exit(1)
	}
	queueDepthSamplePeriod, err := parsePositiveDurationEnv("NOTIFIER_QUEUE_DEPTH_SAMPLE_PERIOD", defaultQueueDepthSamplePeriod)
	if err != nil {
		slog.Error("invalid NOTIFIER_QUEUE_DEPTH_SAMPLE_PERIOD", "err", err)
		os.Exit(1)
	}
	matchNotifyPopTimeout, err := parsePositiveDurationEnv("NOTIFIER_MATCH_NOTIFY_POP_TIMEOUT", redisadapter.DefaultMatchNotifyPopTimeout)
	if err != nil {
		slog.Error("invalid NOTIFIER_MATCH_NOTIFY_POP_TIMEOUT", "err", err)
		os.Exit(1)
	}

	ctx := context.Background()
	shutdownTracer, err := telemetry.InitTracerProvider(ctx, "site-parser-notifier", os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
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
	defer rdb.Close()
	if err := rdb.Ping(ctx).Err(); err != nil {
		slog.Error("redis ping", "err", err)
		os.Exit(1)
	}

	notifRepo := postgres.NewNotificationRepository(pool)
	userRepo := postgres.NewUserRepository(pool)
	jobRepo := postgres.NewJobRepository(pool)
	productEventRepo := postgres.NewProductEventRepository(pool)
	notifier := telegram.NewNotifier(token)
	notifier.Configure(telegram.Config{
		MaxRetries:              notifierMaxRetries,
		RetryBaseWait:           notifierRetryBaseWait,
		BreakerFailureThreshold: breakerFailureThreshold,
		BreakerOpenInterval:     breakerOpenInterval,
		BreakerOpenJitter:       breakerOpenJitter,
	})
	sendNotif := usecase.NewSendNotification(notifRepo, userRepo, jobRepo, notifier, rateLimit, maxPerDay).
		WithProductEventRepo(productEventRepo)

	dailyDigest := usecase.NewDailyDigest(userRepo, notifRepo, jobRepo, notifier, maxPerDay).
		WithProductEventRepo(productEventRepo)
	digestCronSpec := os.Getenv("DIGEST_CRON")
	if digestCronSpec == "" {
		digestCronSpec = "0 * * * *" // каждый час; notifyHour по МСК выбирает нужных пользователей
	}
	digestCron := cron.New()
	if _, err := digestCron.AddFunc(digestCronSpec, func() {
		digestCtx, digestCancel := context.WithTimeout(context.Background(), 5*time.Minute)
		defer digestCancel()
		dailyDigest.Execute(digestCtx)
	}); err != nil {
		slog.Error("digest cron add func", "spec", digestCronSpec, "err", err)
		os.Exit(1)
	}
	digestCron.Start()
	defer digestCron.Stop()
	slog.Info("digest cron started", "spec", digestCronSpec)

	consumer := redisadapter.NewMatchNotifyConsumer(
		rdb,
		queueName,
		redisadapter.WithMatchNotifyPopTimeout(matchNotifyPopTimeout),
	)
	if err := consumer.Recover(ctx); err != nil {
		slog.Error("recover processing queue failed", "queue", queueName, "err", err)
		os.Exit(1)
	}

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

	healthAddr := os.Getenv("NOTIFIER_HEALTH_ADDR")
	if healthAddr == "" {
		healthAddr = ":8082"
	}
	healthMux := http.NewServeMux()
	healthMux.HandleFunc("/healthz", notifierHealthz())
	healthMux.HandleFunc("/readyz", notifierReadyz(pool, notifierRedisClientPinger{client: rdb}))
	healthMux.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	healthSrv := &http.Server{
		Addr:         healthAddr,
		Handler:      healthMux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
	}
	go func() {
		slog.Info("notifier health server listening", "addr", healthAddr)
		if err := healthSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("notifier health server failed", "err", err)
		}
	}()
	go func() {
		<-ctx.Done()
		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutdownCancel()
		_ = healthSrv.Shutdown(shutdownCtx)
	}()

	slog.Info("notifier started", "queue", queueName)
	observeNotifierQueueDepth(rdb, notifierMetrics, queueName)
	queueDepthTicker := time.NewTicker(queueDepthSamplePeriod)
	defer queueDepthTicker.Stop()
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-queueDepthTicker.C:
				observeNotifierQueueDepth(rdb, notifierMetrics, queueName)
			}
		}
	}()

	var popErrBackoff time.Duration
	var pendingRecoverAfterNack bool
	var nackRecoverBackoff time.Duration
	for {
		select {
		case <-ctx.Done():
			slog.Info("notifier stopped")
			return
		default:
			if pendingRecoverAfterNack {
				if err := consumer.Recover(ctx); err != nil {
					nackRecoverBackoff = nextNackRecoverBackoff(nackRecoverBackoff)
					slog.Error(
						"recover processing queue after nack failure failed",
						"queue", queueName,
						"err", err,
						"backoff", nackRecoverBackoff,
					)
					if !waitForBackoff(ctx, nackRecoverBackoff) {
						slog.Info("notifier stopped")
						return
					}
					continue
				}
				slog.Warn("processing queue recovered after nack failure", "queue", queueName)
				pendingRecoverAfterNack = false
				nackRecoverBackoff = 0
			}

			msg, err := consumer.Pop(ctx)
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
			if msg == nil {
				continue
			}
			p := msg.Payload
			msgCtx := ctx
			if p.Traceparent != "" {
				carrier := redisadapter.MapCarrier{"traceparent": p.Traceparent}
				msgCtx = otel.GetTextMapPropagator().Extract(ctx, carrier)
			}
			msgCtx, span := otel.Tracer("site-parser-notifier").Start(msgCtx, "notifier.send")
			var sendErr error
			if len(p.Jobs) > 0 {
				sendErr = sendBatchNotification(msgCtx, sendNotif, p)
			} else {
				sendErr = sendNotif.Execute(
					msgCtx,
					p.UserID,
					p.JobID,
					p.MatchScore,
					p.FinalScore,
					p.RankerVersion,
					p.ReasonCodes,
					p.WhyItFits,
				)
			}
			span.End()
			if sendErr != nil {
				notifierMetrics.ObserveFailed()
				slog.Error(
					"send notification failed",
					"user_id", p.UserID,
					"job_id", p.JobID,
					"batch_jobs", len(p.Jobs),
					"trace_id", p.TraceID,
					"err", sendErr,
				)
				if retryDelay, retryable := telegram.RetryAfter(sendErr); retryable {
					slog.Warn(
						"send notification transient failure; delaying before requeue",
						"user_id", p.UserID,
						"job_id", p.JobID,
						"trace_id", p.TraceID,
						"delay", retryDelay,
					)
					if !waitForBackoff(ctx, retryDelay) {
						slog.Info("notifier stopped")
						return
					}
					if telegram.ShouldRequeueWithoutRetry(sendErr) {
						if requeueErr := consumer.Requeue(ctx, msg); requeueErr != nil {
							slog.Error(
								"requeue without retry increment failed; scheduling processing queue recovery",
								"user_id", p.UserID,
								"job_id", p.JobID,
								"trace_id", p.TraceID,
								"queue", queueName,
								"err", requeueErr,
							)
							pendingRecoverAfterNack = true
						}
						continue
					}
				}
				if nackErr := consumer.Nack(ctx, msg); nackErr != nil {
					slog.Error(
						"nack failed; scheduling processing queue recovery",
						"user_id", p.UserID,
						"job_id", p.JobID,
						"trace_id", p.TraceID,
						"queue", queueName,
						"err", nackErr,
					)
					pendingRecoverAfterNack = true
				}
				continue
			}
			notifierMetrics.ObserveSent()
			if ackErr := consumer.Ack(ctx, msg); ackErr != nil {
				slog.Error("ack failed", "user_id", p.UserID, "job_id", p.JobID, "trace_id", p.TraceID, "err", ackErr)
			}
		}
	}
}

func sendBatchNotification(
	ctx context.Context,
	sendNotif *usecase.SendNotification,
	p port.MatchNotifyPayload,
) error {
	if sendNotif == nil {
		return fmt.Errorf("send notification usecase is nil")
	}
	return sendNotif.ExecuteBatch(ctx, p.UserID, p.Jobs, p.EffectiveBatchScore())
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

func nextNackRecoverBackoff(current time.Duration) time.Duration {
	if current <= 0 {
		return nackRecoverBackoffMin
	}
	next := current * 2
	if next > nackRecoverBackoffMax {
		return nackRecoverBackoffMax
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

func getNotifierMaxPerDay() int {
	// Preferred key for notifier per-day cap; falls back to legacy key for compatibility.
	return getPositiveIntEnvWithFallback("NOTIFY_PRO_MAX_PER_DAY", "NOTIFY_MAX_PER_DAY", 5)
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

func parseFloatEnvInRange(key string, fallback float64, min, max float64) (float64, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return 0, fmt.Errorf("%s must be number: %w", key, err)
	}
	if v < min || v > max {
		return 0, fmt.Errorf("%s must be within [%.2f, %.2f]", key, min, max)
	}
	return v, nil
}

func getPositiveIntEnvWithFallback(primaryKey, secondaryKey string, fallback int) int {
	primaryRaw := os.Getenv(primaryKey)
	if primaryRaw != "" {
		v, err := strconv.Atoi(primaryRaw)
		if err == nil && v > 0 {
			return v
		}
		slog.Warn(
			"invalid env, fallback key/value applied",
			"key", primaryKey,
			"value", primaryRaw,
			"fallback_key", secondaryKey,
			"fallback", fallback,
		)
	}
	return getPositiveIntEnv(secondaryKey, fallback)
}

func getPositiveIntEnv(key string, fallback int) int {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback
	}
	v, err := strconv.Atoi(raw)
	if err != nil || v <= 0 {
		slog.Warn("invalid env, fallback applied", "key", key, "value", raw, "fallback", fallback)
		return fallback
	}
	return v
}

func getDurationEnv(key string, fallback time.Duration) time.Duration {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback
	}
	v, err := time.ParseDuration(raw)
	if err != nil || v <= 0 {
		slog.Warn("invalid env, fallback applied", "key", key, "value", raw, "fallback", fallback)
		return fallback
	}
	return v
}

func getFloatEnvInRange(key string, fallback float64, min, max float64) float64 {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback
	}
	v, err := strconv.ParseFloat(raw, 64)
	if err != nil || v < min || v > max {
		slog.Warn("invalid env, fallback applied", "key", key, "value", raw, "fallback", fallback)
		return fallback
	}
	return v
}

func observeNotifierQueueDepth(client *redisclient.Client, metrics *telemetry.NotifierMetrics, queueName string) {
	if client == nil || metrics == nil || queueName == "" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	ready, err := client.LLen(ctx, queueName).Result()
	if err != nil {
		slog.Warn("notifier queue depth collect failed", "queue", queueName, "err", err)
		return
	}
	processing, err := client.LLen(ctx, queueName+":processing").Result()
	if err != nil {
		slog.Warn("notifier queue depth collect failed", "queue", queueName+":processing", "err", err)
		return
	}
	dlq, err := client.LLen(ctx, queueName+":dlq").Result()
	if err != nil {
		slog.Warn("notifier queue depth collect failed", "queue", queueName+":dlq", "err", err)
		return
	}
	metrics.SetQueueDepth(ready, processing, dlq)
}

type notifierDBPinger interface {
	Ping(ctx context.Context) error
}

type notifierRedisPinger interface {
	Ping(ctx context.Context) error
}

type notifierRedisClientPinger struct {
	client *redisclient.Client
}

func (p notifierRedisClientPinger) Ping(ctx context.Context) error {
	if p.client == nil {
		return fmt.Errorf("redis client is not configured")
	}
	return p.client.Ping(ctx).Err()
}

func notifierHealthz() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}
}

func notifierReadyz(db notifierDBPinger, redis notifierRedisPinger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()

		if db == nil {
			http.Error(w, "db not ready", http.StatusServiceUnavailable)
			return
		}
		if err := db.Ping(ctx); err != nil {
			http.Error(w, "db not ready", http.StatusServiceUnavailable)
			return
		}
		if redis == nil {
			http.Error(w, "redis not ready", http.StatusServiceUnavailable)
			return
		}
		if err := redis.Ping(ctx); err != nil {
			http.Error(w, "redis not ready", http.StatusServiceUnavailable)
			return
		}

		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}
}
