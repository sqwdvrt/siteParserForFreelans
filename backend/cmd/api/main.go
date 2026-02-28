package main

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	redisclient "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisqueue "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/api"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/security"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
)

func main() {
	_ = godotenv.Load()
	_ = godotenv.Load("../.env") // при запуске из backend/

	isProd := security.IsProductionEnv(os.Getenv("APP_ENV"))

	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		fatal("DATABASE_URL not set")
	}
	if err := security.ValidateURLPassword("DATABASE_URL", dbURL, 16); err != nil {
		fatal("invalid DATABASE_URL secret policy", "err", err)
	}
	if isProd {
		if err := security.ValidatePostgresTLSForProduction("DATABASE_URL", dbURL); err != nil {
			fatal("invalid DATABASE_URL transport policy", "err", err)
		}
	}
	apiToken := os.Getenv("API_AUTH_TOKEN")
	if apiToken == "" {
		fatal("API_AUTH_TOKEN not set")
	}
	if err := security.ValidateSecret("API_AUTH_TOKEN", apiToken, 32); err != nil {
		fatal("invalid API_AUTH_TOKEN secret policy", "err", err)
	}
	userHMACSecret := os.Getenv("API_USER_HMAC_SECRET")
	if userHMACSecret == "" {
		fatal("API_USER_HMAC_SECRET not set")
	}
	if err := security.ValidateSecret("API_USER_HMAC_SECRET", userHMACSecret, 32); err != nil {
		fatal("invalid API_USER_HMAC_SECRET secret policy", "err", err)
	}
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379/0"
	}
	allowRedisDegraded, err := parseOptionalBoolEnv("API_ALLOW_REDIS_DEGRADED", os.Getenv("API_ALLOW_REDIS_DEGRADED"))
	if err != nil {
		fatal("invalid API_ALLOW_REDIS_DEGRADED", "err", err)
	}
	if err := validateRuntimeSecurityPolicy(isProd, allowRedisDegraded); err != nil {
		fatal("invalid runtime security policy", "err", err)
	}
	if isProd {
		if err := security.ValidateRedisTLSForProduction("REDIS_URL", redisURL, 16); err != nil {
			fatal("invalid REDIS_URL transport policy", "err", err)
		}
	}

	pool, err := pgxpool.New(context.Background(), dbURL)
	if err != nil {
		fatal("pgxpool init failed", "err", err)
	}
	defer pool.Close()

	userRepo := postgres.NewUserRepository(pool)

	var userEmbedQueue port.UserEmbedQueue
	var nonceStore api.NonceStore
	var rateLimiter api.RequestRateLimiter
	var rdb *redisclient.Client

	opt, err := redisclient.ParseURL(redisURL)
	if err != nil {
		if !allowRedisDegraded {
			fatal("REDIS_URL parse error; refusing insecure degraded mode", "err", err)
		}
		slog.Warn("REDIS_URL parse error; starting in explicitly allowed degraded mode without redis-backed queue/rate-limit/nonce", "err", err)
	} else {
		rdb = redisclient.NewClient(opt)
		if err := rdb.Ping(context.Background()).Err(); err != nil {
			if !allowRedisDegraded {
				fatal("redis unavailable; refusing insecure degraded mode", "err", err)
			}
			slog.Warn("redis unavailable; starting in explicitly allowed degraded mode without redis-backed queue/rate-limit/nonce", "err", err)
			_ = rdb.Close()
			rdb = nil
		} else {
			queueName := os.Getenv("USER_EMBED_QUEUE")
			if queueName == "" {
				queueName = "user-embed"
			}
			userEmbedQueue = redisqueue.NewUserEmbedQueue(rdb, queueName)
			nonceStore = redisqueue.NewNonceStore(rdb, "api:nonce")
			rateLimiter = redisqueue.NewRateLimiter(rdb, "api:ratelimit")
		}
	}
	if rdb != nil {
		defer rdb.Close()
	}

	nonceTTLSec, _ := strconv.Atoi(os.Getenv("API_NONCE_TTL_SEC"))
	if nonceTTLSec <= 0 {
		nonceTTLSec = 600
	}
	rateWindowSec, _ := strconv.Atoi(os.Getenv("API_RATE_LIMIT_WINDOW_SEC"))
	if rateWindowSec <= 0 {
		rateWindowSec = 60
	}
	ipRPM, _ := strconv.Atoi(os.Getenv("API_RATE_LIMIT_IP_RPM"))
	if ipRPM <= 0 {
		ipRPM = 120
	}
	tgRPM, _ := strconv.Atoi(os.Getenv("API_RATE_LIMIT_TG_RPM"))
	if tgRPM <= 0 {
		tgRPM = 60
	}
	trustedProxyCIDRs, err := parseTrustedProxyCIDRs(os.Getenv("API_TRUSTED_PROXY_CIDRS"))
	if err != nil {
		fatal("invalid API_TRUSTED_PROXY_CIDRS", "err", err)
	}

	handlers := &api.Handlers{
		UserRepo:          userRepo,
		UserEmbedQueue:    userEmbedQueue,
		AuthToken:         apiToken,
		UserHMACSecret:    userHMACSecret,
		Logger:            slog.Default(),
		NonceStore:        nonceStore,
		RateLimiter:       rateLimiter,
		TrustedProxyCIDRs: trustedProxyCIDRs,
		NonceTTL:          time.Duration(nonceTTLSec) * time.Second,
		RateLimitWindow:   time.Duration(rateWindowSec) * time.Second,
		IPRateLimit:       ipRPM,
		TelegramRateLimit: tgRPM,
	}

	r := chi.NewRouter()
	registry := prometheus.NewRegistry()
	registry.MustRegister(
		collectors.NewGoCollector(),
		collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}),
	)
	httpMetrics := telemetry.NewHTTPMetrics(registry)
	r.Use(httpMetrics.Middleware)
	r.Use(api.RequestLoggingMiddleware(slog.Default()))
	var redisHealth redisPinger
	if rdb != nil {
		redisHealth = redisClientPinger{client: rdb}
	}
	r.Get("/healthz", healthz())
	r.Get("/readyz", readyz(pool, redisHealth))
	r.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	r.Post("/users", handlers.PostUsers)
	r.Put("/users/{id}/profile", handlers.PutUserProfile)

	addr := os.Getenv("API_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	tlsCertFile := strings.TrimSpace(os.Getenv("API_TLS_CERT_FILE"))
	tlsKeyFile := strings.TrimSpace(os.Getenv("API_TLS_KEY_FILE"))
	useTLS := false
	if tlsCertFile != "" || tlsKeyFile != "" {
		if tlsCertFile == "" || tlsKeyFile == "" {
			fatal("both API_TLS_CERT_FILE and API_TLS_KEY_FILE must be set")
		}
		useTLS = true
	}
	if isProd && !useTLS {
		fatal("APP_ENV=production requires API TLS: set API_TLS_CERT_FILE and API_TLS_KEY_FILE")
	}

	srv := &http.Server{
		Addr:              addr,
		Handler:           r,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	go func() {
		if useTLS {
			slog.Info("API listening with TLS", "addr", addr)
			if err := srv.ListenAndServeTLS(tlsCertFile, tlsKeyFile); err != nil && err != http.ErrServerClosed {
				fatal("API listen TLS failed", "err", err)
			}
			return
		}
		slog.Info("API listening", "addr", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			fatal("API listen failed", "err", err)
		}
	}()
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	signal.Stop(sigCh)
	slog.Info("shutdown signal received")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		slog.Error("API shutdown failed", "err", err)
	}
	slog.Info("API stopped")
}

func parseTrustedProxyCIDRs(raw string) ([]*net.IPNet, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	parts := strings.Split(raw, ",")
	out := make([]*net.IPNet, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		_, n, err := net.ParseCIDR(p)
		if err != nil {
			return nil, fmt.Errorf("%q: %w", p, err)
		}
		out = append(out, n)
	}
	return out, nil
}

type redisPinger interface {
	Ping(ctx context.Context) error
}

type dbPinger interface {
	Ping(ctx context.Context) error
}

type redisClientPinger struct {
	client *redisclient.Client
}

func (p redisClientPinger) Ping(ctx context.Context) error {
	if p.client == nil {
		return fmt.Errorf("redis client is not configured")
	}
	return p.client.Ping(ctx).Err()
}

func healthz() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}
}

func readyz(db dbPinger, redis redisPinger) http.HandlerFunc {
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

func fatal(msg string, args ...any) {
	slog.Error(msg, args...)
	os.Exit(1)
}

func parseOptionalBoolEnv(name, raw string) (bool, error) {
	v := strings.ToLower(strings.TrimSpace(raw))
	switch v {
	case "", "0", "false", "no", "off":
		return false, nil
	case "1", "true", "yes", "on":
		return true, nil
	default:
		return false, fmt.Errorf("%s must be boolean (accepted: 1/0, true/false, yes/no, on/off)", name)
	}
}

func validateRuntimeSecurityPolicy(isProd, allowRedisDegraded bool) error {
	if isProd && allowRedisDegraded {
		return fmt.Errorf("APP_ENV=production forbids API_ALLOW_REDIS_DEGRADED=1; redis-backed nonce/rate-limit/user-embed queue are mandatory")
	}
	return nil
}
