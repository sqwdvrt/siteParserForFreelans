"""PostgreSQL implementation of MatchRepository."""

from __future__ import annotations

from pgvector import Vector
from psycopg2.extras import RealDictCursor

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.domain.embedding import EMBEDDING_DIM
from ai_service.port.match_repository import MatchCandidate, MatchRepository

KWORK_MAX_LAST_SEEN_AGE_SQL = "INTERVAL '6 hours'"


class PostgresMatchRepository(PooledPostgresRepository, MatchRepository):
    """MatchRepository via PostgreSQL + pgvector.

    Uses a two-stage ANN approach:
    1. HNSW ANN retrieves top-K candidates by vector similarity (fast)
    2. SQL filters apply preference constraints on the candidate set

    This is significantly faster than loading ALL users into Python for
    pre-filtering, especially as the user base grows.
    """

    def __init__(
        self,
        dsn: str,
        *,
        minconn: int = 1,
        maxconn: int = 10,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        super().__init__(dsn, minconn=minconn, maxconn=maxconn, statement_timeout_ms=statement_timeout_ms)

    def find_users_for_job(
        self,
        embedding: list[float],
        job_id: int,
        threshold: float,
        limit: int = 100,
        allowed_user_ids: list[int] | None = None,
        max_age_days: int | None = None,
    ) -> list[MatchCandidate]:
        """Find matching users for a job using two-stage ANN + SQL filtering.

        Stage 1: HNSW ANN retrieves top candidates by embedding similarity.
        Stage 2: SQL filters (source, paused, allowed_user_ids) narrow results.

        If allowed_user_ids is provided, it's used as a pre-filter (legacy mode).
        Otherwise, the ANN-first approach fetches candidates and filters in SQL.
        """
        if embedding is None or len(embedding) == 0 or len(embedding) != EMBEDDING_DIM:
            return []
        if allowed_user_ids is not None and not allowed_user_ids:
            return []

        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                vec = Vector(embedding)
                user_scope_sql = ""
                job_age_sql = ""
                params: list[object] = [vec]

                if allowed_user_ids is not None:
                    # Legacy mode: pre-filtered user IDs from Python
                    user_scope_sql = " AND u.id = ANY(%s)"
                    params.append(allowed_user_ids)

                params.extend([job_id, threshold])
                if max_age_days is not None and max_age_days > 0:
                    job_age_sql = " AND COALESCE(j.posted_at, j.created_at) >= NOW() - make_interval(days => %s)"
                    params.append(max_age_days)

                # Expanded pool for SQL filtering (when no pre-filter)
                ann_pool_size = limit * 5 if allowed_user_ids is None else limit

                params.append(ann_pool_size)
                params.append(limit)

                cur.execute(
                    f"""
                    WITH ann_candidates AS (
                      SELECT
                        u.id AS user_id,
                        COALESCE(u.profile_text, '') AS profile_text,
                        COALESCE(u.paused_until, NOW() - INTERVAL '1 day') < NOW() AS is_active,
                        1 - (u.embedding <=> %s) AS similarity
                      FROM users u
                      WHERE u.embedding IS NOT NULL
                      {user_scope_sql}
                      ORDER BY u.embedding <=> %s
                      LIMIT %s
                    )
                    SELECT s.user_id, s.profile_text, s.similarity
                    FROM ann_candidates s
                    JOIN jobs j ON j.id = %s
                    WHERE j.status = 'active'
                      AND (j.source <> 'kwork' OR j.last_seen_at >= NOW() - {KWORK_MAX_LAST_SEEN_AGE_SQL})
                      AND s.similarity >= %s
                      AND s.is_active
                      {job_age_sql}
                      AND NOT EXISTS (
                          SELECT 1
                          FROM notifications n
                          WHERE n.job_id = j.id
                            AND n.user_id = s.user_id
                      )
                    ORDER BY s.similarity DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        return [
            MatchCandidate(
                user_id=row["user_id"],
                job_id=job_id,
                match_score=float(row["similarity"]),
                rerank_score=0.0,
                raw_similarity=float(row["similarity"]),
                final_score=float(row["similarity"]),
                profile_text=str(row.get("profile_text") or ""),
            )
            for row in rows
        ]

    def find_jobs_for_user(
        self,
        embedding: list[float],
        user_id: int,
        threshold: float,
        limit: int = 100,
        days_back: int = 7,
    ) -> list[MatchCandidate]:
        """Find recent jobs for user by similarity, excluding already notified jobs."""
        if embedding is None or len(embedding) == 0 or len(embedding) != EMBEDDING_DIM:
            return []
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                vec = Vector(embedding)
                cur.execute(
                    f"""
                    WITH scored_jobs AS (
                      SELECT
                        j.id AS job_id,
                        1 - (je.embedding <=> %s) AS similarity
                      FROM jobs j
                      JOIN job_embeddings je ON je.job_id = j.id
                      WHERE j.status = 'active'
                        AND (j.source <> 'kwork' OR j.last_seen_at >= NOW() - {KWORK_MAX_LAST_SEEN_AGE_SQL})
                        AND COALESCE(j.posted_at, j.created_at) >= NOW() - make_interval(days => %s)
                    )
                    SELECT s.job_id, s.similarity
                    FROM scored_jobs s
                    WHERE s.similarity >= %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM notifications n
                          WHERE n.user_id = %s
                            AND n.job_id = s.job_id
                      )
                    ORDER BY s.similarity DESC
                    LIMIT %s
                    """,
                    (vec, days_back, threshold, user_id, limit),
                )
                rows = cur.fetchall()
        return [
            MatchCandidate(
                user_id=user_id,
                job_id=row["job_id"],
                match_score=float(row["similarity"]),
                rerank_score=0.0,
                raw_similarity=float(row["similarity"]),
                final_score=float(row["similarity"]),
            )
            for row in rows
        ]
