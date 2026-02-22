package main

import (
	"context"
	"fmt"
	"log"
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
	redisclient "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisqueue "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/api"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/security"
)

func main() {
	_ = godotenv.Load()
	_ = godotenv.Load("../.env") // при запуске из backend/

	isProd := security.IsProductionEnv(os.Getenv("APP_ENV"))

	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		log.Fatal("DATABASE_URL not set")
	}
	if err := security.ValidateURLPassword("DATABASE_URL", dbURL, 16); err != nil {
		log.Fatalf("invalid DATABASE_URL secret policy: %v", err)
	}
	if isProd {
		if err := security.ValidatePostgresTLSForProduction("DATABASE_URL", dbURL); err != nil {
			log.Fatalf("invalid DATABASE_URL transport policy: %v", err)
		}
	}
	apiToken := os.Getenv("API_AUTH_TOKEN")
	if apiToken == "" {
		log.Fatal("API_AUTH_TOKEN not set")
	}
	if err := security.ValidateSecret("API_AUTH_TOKEN", apiToken, 32); err != nil {
		log.Fatalf("invalid API_AUTH_TOKEN secret policy: %v", err)
	}
	userHMACSecret := os.Getenv("API_USER_HMAC_SECRET")
	if userHMACSecret == "" {
		log.Fatal("API_USER_HMAC_SECRET not set")
	}
	if err := security.ValidateSecret("API_USER_HMAC_SECRET", userHMACSecret, 32); err != nil {
		log.Fatalf("invalid API_USER_HMAC_SECRET secret policy: %v", err)
	}
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379/0"
	}
	allowRedisDegraded, err := parseOptionalBoolEnv("API_ALLOW_REDIS_DEGRADED", os.Getenv("API_ALLOW_REDIS_DEGRADED"))
	if err != nil {
		log.Fatal(err)
	}
	if err := validateRuntimeSecurityPolicy(isProd, allowRedisDegraded); err != nil {
		log.Fatal(err)
	}
	if isProd {
		if err := security.ValidateRedisTLSForProduction("REDIS_URL", redisURL, 16); err != nil {
			log.Fatalf("invalid REDIS_URL transport policy: %v", err)
		}
	}

	pool, err := pgxpool.New(context.Background(), dbURL)
	if err != nil {
		log.Fatalf("pgxpool: %v", err)
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
			log.Fatalf(
				"REDIS_URL parse error (%v); refusing insecure degraded mode: set API_ALLOW_REDIS_DEGRADED=1 to explicitly allow start without redis-backed queue/rate-limit/nonce",
				err,
			)
		}
		log.Printf("REDIS_URL parse error (%v); starting API in EXPLICITLY ALLOWED degraded mode without redis-backed queue/rate-limit/nonce", err)
	} else {
		rdb = redisclient.NewClient(opt)
		if err := rdb.Ping(context.Background()).Err(); err != nil {
			if !allowRedisDegraded {
				log.Fatalf(
					"redis unavailable (%v); refusing insecure degraded mode: set API_ALLOW_REDIS_DEGRADED=1 to explicitly allow start without redis-backed queue/rate-limit/nonce",
					err,
				)
			}
			log.Printf("redis unavailable (%v); starting API in EXPLICITLY ALLOWED degraded mode without redis-backed queue/rate-limit/nonce", err)
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
		log.Fatalf("invalid API_TRUSTED_PROXY_CIDRS: %v", err)
	}

	handlers := &api.Handlers{
		UserRepo:          userRepo,
		UserEmbedQueue:    userEmbedQueue,
		AuthToken:         apiToken,
		UserHMACSecret:    userHMACSecret,
		NonceStore:        nonceStore,
		RateLimiter:       rateLimiter,
		TrustedProxyCIDRs: trustedProxyCIDRs,
		NonceTTL:          time.Duration(nonceTTLSec) * time.Second,
		RateLimitWindow:   time.Duration(rateWindowSec) * time.Second,
		IPRateLimit:       ipRPM,
		TelegramRateLimit: tgRPM,
	}

	r := chi.NewRouter()
	var redisHealth redisPinger
	if rdb != nil {
		redisHealth = redisClientPinger{client: rdb}
	}
	r.Get("/healthz", healthz(redisHealth))
	r.Get("/readyz", readyz(pool, redisHealth))
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
			log.Fatal("both API_TLS_CERT_FILE and API_TLS_KEY_FILE must be set")
		}
		useTLS = true
	}
	if isProd && !useTLS {
		log.Fatal("APP_ENV=production requires API TLS: set API_TLS_CERT_FILE and API_TLS_KEY_FILE")
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
			log.Printf("API listening with TLS on %s", addr)
			if err := srv.ListenAndServeTLS(tlsCertFile, tlsKeyFile); err != nil && err != http.ErrServerClosed {
				log.Fatalf("API listen TLS: %v", err)
			}
			return
		}
		log.Printf("API listening on %s", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("API listen: %v", err)
		}
	}()
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	signal.Stop(sigCh)
	log.Println("shutdown signal received")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Printf("API shutdown: %v", err)
	}
	log.Println("API stopped")
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

func healthz(redis redisPinger) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()

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
