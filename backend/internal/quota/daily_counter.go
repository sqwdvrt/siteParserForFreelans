package quota

import (
	"context"
	"fmt"
	"strconv"
	"time"

	redisclient "github.com/redis/go-redis/v9"
)

const (
	// DailyCounterKeyPrefix — префикс ключей для дневных счётчиков (напр. "daily:orders:123").
	DailyCounterKeyPrefix = "daily:orders:"
	// DailyCounterTTL — TTL для дневных счётчиков (24 часа + 1 час на случай, если Redis живёт в другом часовом поясе).
	DailyCounterTTL = 25 * time.Hour
)

// DailyCounter — сервис для учёта количества отправленных заказов за день.

type DailyCounter struct {
	client *redisclient.Client
}

// NewDailyCounter — создаёт новый DailyCounter.

func NewDailyCounter(client *redisclient.Client) *DailyCounter {
	return &DailyCounter{
		client: client,
	}
}

// Increment — инкрементирует счётчик для пользователя на 1 и возвращает новое значение.

func (c *DailyCounter) Increment(ctx context.Context, userID int64) (int, error) {
	key := c.buildKey(userID)
	pipe := c.client.Pipeline()
	incr := pipe.Incr(ctx, key)
	pipe.Expire(ctx, key, DailyCounterTTL)
	_, err := pipe.Exec(ctx)
	if err != nil {
		return 0, fmt.Errorf("increment daily counter failed for user %d: %w", userID, err)
	}
	return int(incr.Val()), nil
}

// Get — возвращает текущее значение счётчика для пользователя. 0 если нет или истёк.

func (c *DailyCounter) Get(ctx context.Context, userID int64) (int, error) {
	key := c.buildKey(userID)
	val, err := c.client.Get(ctx, key).Result()
	if err != nil {
		if err == redisclient.Nil {
			return 0, nil
		}
		return 0, fmt.Errorf("get daily counter failed for user %d: %w", userID, err)
	}
	count, err := strconv.Atoi(val)
	if err != nil {
		return 0, fmt.Errorf("parse daily counter value failed for user %d: %w", userID, err)
	}
	return count, nil
}

func (c *DailyCounter) buildKey(userID int64) string {
	return fmt.Sprintf("%s%d", DailyCounterKeyPrefix, userID)
}