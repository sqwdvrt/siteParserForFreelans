package redis

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"time"

	goredis "github.com/redis/go-redis/v9"
)

const defaultNoncePrefix = "api:nonce"
const defaultRateLimitPrefix = "api:ratelimit"
const defaultLockTTL = 10 * time.Minute

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

var lockReleaseScript = goredis.NewScript(`
local key = KEYS[1]
local token = ARGV[1]
if redis.call("GET", key) == token then
  return redis.call("DEL", key)
end
return 0
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

// RedisLock implements a small Redis-backed lease for leader election.
type RedisLock struct {
	client *goredis.Client
}

// RedisLease represents an acquired lock token.
type RedisLease struct {
	client *goredis.Client
	key    string
	token  string
}

// NewRedisLock creates a Redis-backed lock helper.
func NewRedisLock(client *goredis.Client) *RedisLock {
	return &RedisLock{client: client}
}

// Acquire tries to claim a lock key with a unique token and TTL.
func (l *RedisLock) Acquire(ctx context.Context, key string, ttl time.Duration) (*RedisLease, bool, error) {
	if ttl <= 0 {
		ttl = defaultLockTTL
	}
	token, err := newLockToken()
	if err != nil {
		return nil, false, err
	}
	ok, err := l.client.SetNX(ctx, key, token, ttl).Result()
	if err != nil {
		return nil, false, err
	}
	if !ok {
		return nil, false, nil
	}
	return &RedisLease{client: l.client, key: key, token: token}, true, nil
}

// Release deletes the lock only if the stored token still matches the lease.
func (l *RedisLease) Release(ctx context.Context) (bool, error) {
	if l == nil || l.client == nil || l.key == "" || l.token == "" {
		return false, nil
	}
	res, err := lockReleaseScript.Run(ctx, l.client, []string{l.key}, l.token).Int()
	if err != nil {
		return false, err
	}
	return res == 1, nil
}

func newLockToken() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}
