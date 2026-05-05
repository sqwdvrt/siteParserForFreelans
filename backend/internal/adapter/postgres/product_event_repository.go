package postgres

import (
	"context"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

// ProductEventRepository реализует port.ProductEventRepository через PostgreSQL.
type ProductEventRepository struct {
	pool *pgxpool.Pool
}

// NewProductEventRepository создаёт репозиторий product_events.
func NewProductEventRepository(pool *pgxpool.Pool) *ProductEventRepository {
	return &ProductEventRepository{pool: pool}
}

// Record сохраняет единичное событие продуктовой аналитики.
func (r *ProductEventRepository) Record(ctx context.Context, event port.ProductEvent) error {
	properties := event.Properties
	if properties == nil {
		properties = map[string]any{}
	}
	payload, err := json.Marshal(properties)
	if err != nil {
		return err
	}

	_, err = r.pool.Exec(ctx, `
		INSERT INTO product_events (event_type, user_id, job_id, source, properties)
		VALUES ($1, NULLIF($2, 0), NULLIF($3, 0), NULLIF($4, ''), $5::jsonb)
	`, string(event.Type), event.UserID, event.JobID, event.Source, payload)
	return err
}

func (r *ProductEventRepository) ExistsSince(
	ctx context.Context,
	userID int64,
	eventType port.ProductEventType,
	since time.Time,
	propertyKey,
	propertyValue string,
) (bool, error) {
	var exists bool
	err := r.pool.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM product_events
			WHERE user_id = $1
			  AND event_type = $2
			  AND created_at >= $3
			  AND (
				NULLIF($4, '') IS NULL
				OR COALESCE(properties ->> $4, '') = $5
			  )
		)
	`, userID, string(eventType), since, propertyKey, propertyValue).Scan(&exists)
	return exists, err
}

var _ port.ProductEventRepository = (*ProductEventRepository)(nil)
