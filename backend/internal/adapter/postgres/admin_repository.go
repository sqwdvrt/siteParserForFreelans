package postgres

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const adminProfileSnippetLen = 200

// AdminRepository реализует port.AdminRepository для PostgreSQL.
type AdminRepository struct {
	pool *pgxpool.Pool
}

// NewAdminRepository создаёт репозиторий для admin-операций.
func NewAdminRepository(pool *pgxpool.Pool) *AdminRepository {
	return &AdminRepository{pool: pool}
}

// GetStats возвращает агрегированную статистику системы.
func (r *AdminRepository) GetStats(ctx context.Context) (*port.AdminStats, error) {
	var s port.AdminStats

	row := r.pool.QueryRow(ctx, `
		SELECT
			COUNT(*)                                                    AS total_users,
			COUNT(*) FILTER (WHERE profile_text IS NOT NULL
			                   AND profile_text <> '')                  AS users_with_profile,
			COUNT(*) FILTER (WHERE embedding IS NOT NULL)               AS users_with_embedding
		FROM users
	`)
	if err := row.Scan(&s.TotalUsers, &s.UsersWithProfile, &s.UsersWithEmbedding); err != nil {
		return nil, err
	}

	row = r.pool.QueryRow(ctx, `
		SELECT
			COUNT(*)                                                  AS total_jobs,
			COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS jobs_last_7d
		FROM jobs
	`)
	if err := row.Scan(&s.TotalJobs, &s.JobsLast7Days); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `SELECT COUNT(*) FROM job_embeddings`).
		Scan(&s.TotalJobEmbeddings); err != nil {
		return nil, err
	}

	row = r.pool.QueryRow(ctx, `
		SELECT
			COUNT(*) FILTER (WHERE status = 'sent')    AS total_sent,
			COUNT(*) FILTER (WHERE status = 'sent'
			                   AND sent_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')) AS today,
			COUNT(*) FILTER (WHERE status = 'pending') AS pending
		FROM notifications
	`)
	if err := row.Scan(&s.TotalNotificationsSent, &s.NotificationsToday, &s.PendingNotifications); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM pending_ac_jobs WHERE processed_at IS NULL
	`).Scan(&s.PendingACJobs); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `
		SELECT COALESCE(AVG(bad_ratio), 0)
		FROM (
			SELECT
				user_id,
				COUNT(*) FILTER (WHERE feedback = 'bad')::float / NULLIF(COUNT(*), 0)::float AS bad_ratio
			FROM user_feedback
			GROUP BY user_id
		) ratios
	`).Scan(&s.AvgBadFeedbackRate); err != nil {
		return nil, err
	}

	if err := r.pool.QueryRow(ctx, `
		SELECT COALESCE(
			COUNT(*) FILTER (
				WHERE status = 'sent'
				  AND EXISTS (
					  SELECT 1
					  FROM user_feedback uf
					  WHERE uf.user_id = n.user_id
					    AND uf.job_id = n.job_id
				  )
			)::float / NULLIF(COUNT(*) FILTER (WHERE status = 'sent'), 0)::float,
			0
		)
		FROM notifications n
	`).Scan(&s.FeedbackConversionRate); err != nil {
		return nil, err
	}

	s.FinalScoreDistribution = map[string]int64{
		"lt_0_40":   0,
		"0_40_0_60": 0,
		"0_60_0_80": 0,
		"gte_0_80":  0,
	}
	var lt040, bucket040060, bucket060080, gte080 int64
	if err := r.pool.QueryRow(ctx, `
		SELECT
			COUNT(*) FILTER (WHERE final_score IS NOT NULL AND final_score < 0.4)                           AS lt_0_40,
			COUNT(*) FILTER (WHERE final_score >= 0.4 AND final_score < 0.6)                                AS bucket_0_40_0_60,
			COUNT(*) FILTER (WHERE final_score >= 0.6 AND final_score < 0.8)                                AS bucket_0_60_0_80,
			COUNT(*) FILTER (WHERE final_score >= 0.8)                                                       AS gte_0_80
		FROM notifications
	`).Scan(
		&lt040,
		&bucket040060,
		&bucket060080,
		&gte080,
	); err != nil {
		return nil, err
	}
	s.FinalScoreDistribution["lt_0_40"] = lt040
	s.FinalScoreDistribution["0_40_0_60"] = bucket040060
	s.FinalScoreDistribution["0_60_0_80"] = bucket060080
	s.FinalScoreDistribution["gte_0_80"] = gte080

	return &s, nil
}

// ListUsers возвращает постраничный список пользователей с числом отправленных уведомлений.
func (r *AdminRepository) ListUsers(ctx context.Context, limit, offset int) ([]port.AdminUser, int64, error) {
	var total int64
	if err := r.pool.QueryRow(ctx, `SELECT COUNT(*) FROM users`).Scan(&total); err != nil {
		return nil, 0, err
	}

	rows, err := r.pool.Query(ctx, `
		SELECT
			u.id,
			u.telegram_id,
			u.profile_text IS NOT NULL AND u.profile_text <> ''  AS has_profile,
			u.embedding IS NOT NULL                               AS has_embedding,
			u.created_at,
			u.updated_at,
			u.profile_text,
			COALESCE(
				(SELECT COUNT(*) FROM notifications n
				 WHERE n.user_id = u.id AND n.status = 'sent'), 0
			) AS notifications_sent
		FROM users u
		ORDER BY u.id DESC
		LIMIT $1 OFFSET $2
	`, limit, offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	var users []port.AdminUser
	for rows.Next() {
		var u port.AdminUser
		var profileText *string
		if err := rows.Scan(
			&u.ID, &u.TelegramID,
			&u.HasProfile, &u.HasEmbedding,
			&u.CreatedAt, &u.UpdatedAt,
			&profileText,
			&u.NotificationsSent,
		); err != nil {
			return nil, 0, err
		}
		if profileText != nil {
			u.ProfileSnippet = truncateString(*profileText, adminProfileSnippetLen)
		}
		users = append(users, u)
	}
	return users, total, rows.Err()
}

// GetUser возвращает одного пользователя по id. nil если не найден.
func (r *AdminRepository) GetUser(ctx context.Context, userID int64) (*port.AdminUser, error) {
	var u port.AdminUser
	var profileText *string

	err := r.pool.QueryRow(ctx, `
		SELECT
			u.id,
			u.telegram_id,
			u.profile_text IS NOT NULL AND u.profile_text <> ''  AS has_profile,
			u.embedding IS NOT NULL                               AS has_embedding,
			u.created_at,
			u.updated_at,
			u.profile_text,
			COALESCE(
				(SELECT COUNT(*) FROM notifications n
				 WHERE n.user_id = u.id AND n.status = 'sent'), 0
			) AS notifications_sent
		FROM users u
		WHERE u.id = $1
	`, userID).Scan(
		&u.ID, &u.TelegramID,
		&u.HasProfile, &u.HasEmbedding,
		&u.CreatedAt, &u.UpdatedAt,
		&profileText,
		&u.NotificationsSent,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	if profileText != nil {
		u.ProfileSnippet = truncateString(*profileText, adminProfileSnippetLen)
	}
	return &u, nil
}

// DeleteUser удаляет пользователя по id. bool=true если запись была найдена и удалена.
func (r *AdminRepository) DeleteUser(ctx context.Context, userID int64) (bool, error) {
	tag, err := r.pool.Exec(ctx, `DELETE FROM users WHERE id = $1`, userID)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() > 0, nil
}

// ListJobs возвращает постраничный список задач. source="" — все источники.
func (r *AdminRepository) ListJobs(ctx context.Context, source string, limit, offset int) ([]port.AdminJob, int64, error) {
	var total int64
	if source != "" {
		if err := r.pool.QueryRow(ctx,
			`SELECT COUNT(*) FROM jobs WHERE source = $1`, source,
		).Scan(&total); err != nil {
			return nil, 0, err
		}
	} else {
		if err := r.pool.QueryRow(ctx, `SELECT COUNT(*) FROM jobs`).Scan(&total); err != nil {
			return nil, 0, err
		}
	}

	var rows pgx.Rows
	var err error
	if source != "" {
		rows, err = r.pool.Query(ctx, `
			SELECT
				j.id, j.source, j.title, j.url,
				COALESCE(j.budget, '')  AS budget,
				je.job_id IS NOT NULL   AS has_embedding,
				j.posted_at,
				j.created_at
			FROM jobs j
			LEFT JOIN job_embeddings je ON je.job_id = j.id
			WHERE j.source = $1
			ORDER BY j.id DESC
			LIMIT $2 OFFSET $3
		`, source, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx, `
			SELECT
				j.id, j.source, j.title, j.url,
				COALESCE(j.budget, '')  AS budget,
				je.job_id IS NOT NULL   AS has_embedding,
				j.posted_at,
				j.created_at
			FROM jobs j
			LEFT JOIN job_embeddings je ON je.job_id = j.id
			ORDER BY j.id DESC
			LIMIT $1 OFFSET $2
		`, limit, offset)
	}
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	var jobs []port.AdminJob
	for rows.Next() {
		var j port.AdminJob
		if err := rows.Scan(
			&j.ID, &j.Source, &j.Title, &j.URL,
			&j.Budget, &j.HasEmbed, &j.PostedAt, &j.CreatedAt,
		); err != nil {
			return nil, 0, err
		}
		jobs = append(jobs, j)
	}
	return jobs, total, rows.Err()
}

// truncateString обрезает строку до maxRunes рун.
func truncateString(s string, maxRunes int) string {
	count := 0
	for i := range s {
		if count == maxRunes {
			return s[:i] + "…"
		}
		count++
	}
	return s
}
