"""PostgreSQL implementation of MatchRepository."""

from __future__ import annotations

from pgvector import Vector
from psycopg2.extras import RealDictCursor

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.port.match_repository import MatchCandidate, MatchRepository


class PostgresMatchRepository(PooledPostgresRepository, MatchRepository):
    """MatchRepository через PostgreSQL + pgvector."""

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
    ) -> list[MatchCandidate]:
        """
        SQL: score = 1 - (embedding <=> $1) считается в CTE.
        WHERE embedding IS NOT NULL, фильтрация по готовому score >= threshold.
        Исключает user_id уже в notifications для job_id.
        ORDER BY score DESC. match_score = similarity (0–1).
        """
        if embedding is None or len(embedding) == 0 or len(embedding) != 384:
            return []
        if allowed_user_ids is not None and not allowed_user_ids:
            return []
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                vec = Vector(embedding)
                user_scope_sql = ""
                params: list[object] = [vec]
                if allowed_user_ids is not None:
                    user_scope_sql = " AND u.id = ANY(%s)"
                    params.append(allowed_user_ids)
                params.extend([threshold, job_id, limit])
                cur.execute(
                    f"""
                    WITH scored_users AS (
                      SELECT
                        u.id AS user_id,
                        COALESCE(u.profile_text, '') AS profile_text,
                        1 - (u.embedding <=> %s) AS similarity
                      FROM users u
                      WHERE u.embedding IS NOT NULL
                      {user_scope_sql}
                    )
                    SELECT s.user_id, s.profile_text, s.similarity
                    FROM scored_users s
                    WHERE s.similarity >= %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM notifications n
                          WHERE n.job_id = %s
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
        if embedding is None or len(embedding) == 0 or len(embedding) != 384:
            return []
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                vec = Vector(embedding)
                cur.execute(
                    """
                    WITH scored_jobs AS (
                      SELECT
                        j.id AS job_id,
                        1 - (je.embedding <=> %s) AS similarity
                      FROM jobs j
                      JOIN job_embeddings je ON je.job_id = j.id
                      WHERE COALESCE(j.posted_at, j.created_at) >= NOW() - make_interval(days => %s)
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
