package redis

import (
	"context"
	"time"

	goredis "github.com/redis/go-redis/v9"
)

const defaultNoncePrefix = "api:nonce"
const defaultRateLimitPrefix = "api:ratelimit"

var rateLimitScript = goredis.NewScript(`
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local current = redis.call("INCR", key)
if current == 1 then
  redis.call("PEXPIRE", key, ttl_ms)
end
if current > limit then
  return 0
end
return 1
`)

// NonceStore хранит одноразовые nonce в Redis (SET NX + TTL).
type NonceStore struct {
	client *goredis.Client
	prefix string
}

// NewNonceStore создаёт Redis NonceStore.
func NewNonceStore(client *goredis.Client, prefix string) *NonceStore {
	if prefix == "" {
		prefix = defaultNoncePrefix
	}
	return &NonceStore{
		client: client,
		prefix: prefix,
	}
}

// Use резервирует nonce-ключ на ttl. Возвращает false при повторном использовании.
func (s *NonceStore) Use(ctx context.Context, key string, ttl time.Duration) (bool, error) {
	if ttl <= 0 {
		ttl = 10 * time.Minute
	}
	return s.client.SetNX(ctx, s.prefix+":"+key, "1", ttl).Result()
}

// RateLimiter реализует fixed-window rate limit через Redis.
type RateLimiter struct {
	client *goredis.Client
	prefix string
}

// NewRateLimiter создаёт Redis RateLimiter.
func NewRateLimiter(client *goredis.Client, prefix string) *RateLimiter {
	if prefix == "" {
		prefix = defaultRateLimitPrefix
	}
	return &RateLimiter{
		client: client,
		prefix: prefix,
	}
}

// Allow проверяет лимит для ключа в окне window.
func (r *RateLimiter) Allow(ctx context.Context, key string, limit int, window time.Duration) (bool, error) {
	if limit <= 0 || window <= 0 {
		return true, nil
	}
	res, err := rateLimitScript.Run(
		ctx,
		r.client,
		[]string{r.prefix + ":" + key},
		limit,
		window.Milliseconds(),
	).Int()
	if err != nil {
		return false, err
	}
	return res == 1, nil
}
