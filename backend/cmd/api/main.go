package main

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	redisclient "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/postgres"
	redisqueue "github.com/sqwdvrt/siteParserForFreelans/backend/internal/adapter/redis"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/api"
)

func main() {
	_ = godotenv.Load()
	_ = godotenv.Load("../.env") // при запуске из backend/

	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		log.Fatal("DATABASE_URL not set")
	}

	pool, err := pgxpool.New(context.Background(), dbURL)
	if err != nil {
		log.Fatalf("pgxpool: %v", err)
	}
	defer pool.Close()

	userRepo := postgres.NewUserRepository(pool)

	var userEmbedQueue *redisqueue.UserEmbedQueue
	if redisURL := os.Getenv("REDIS_URL"); redisURL != "" {
		opt, err := redisclient.ParseURL(redisURL)
		if err != nil {
			log.Fatalf("REDIS_URL: %v", err)
		}
		rdb := redisclient.NewClient(opt)
		if err := rdb.Ping(context.Background()).Err(); err != nil {
			log.Fatalf("redis ping: %v", err)
		}
		queueName := os.Getenv("USER_EMBED_QUEUE")
		if queueName == "" {
			queueName = "user-embed"
		}
		userEmbedQueue = redisqueue.NewUserEmbedQueue(rdb, queueName)
	}

	handlers := &api.Handlers{UserRepo: userRepo, UserEmbedQueue: userEmbedQueue}

	r := chi.NewRouter()
	r.Post("/users", handlers.PostUsers)
	r.Put("/users/{id}/profile", handlers.PutUserProfile)

	addr := os.Getenv("API_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	log.Printf("API listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, r))
}
