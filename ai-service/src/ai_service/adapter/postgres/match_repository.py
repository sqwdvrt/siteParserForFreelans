"""PostgreSQL implementation of MatchRepository."""

from __future__ import annotations

from pgvector import Vector
from psycopg2.extras import RealDictCursor

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.port.match_repository import MatchCandidate, MatchRepository


class PostgresMatchRepository(PooledPostgresRepository, MatchRepository):
    """MatchRepository через PostgreSQL + pgvector."""

    def __init__(self, dsn: str, *, minconn: int = 1, maxconn: int = 10) -> None:
        super().__init__(dsn, minconn=minconn, maxconn=maxconn)

    def find_users_for_job(
        self,
        embedding: list[float],
        job_id: int,
        threshold: float,
        limit: int = 100,
    ) -> list[MatchCandidate]:
        """
        SQL: score = 1 - (embedding <=> $1) считается в CTE.
        WHERE embedding IS NOT NULL, фильтрация по готовому score >= threshold.
        Исключает user_id уже в notifications для job_id.
        ORDER BY score DESC. match_score = similarity (0–1).
        """
        if embedding is None or len(embedding) == 0 or len(embedding) != 384:
            return []
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                vec = Vector(embedding)
                cur.execute(
                    """
                    WITH scored_users AS (
                      SELECT
                        u.id AS user_id,
                        1 - (u.embedding <=> %s) AS similarity
                      FROM users u
                      WHERE u.embedding IS NOT NULL
                    )
                    SELECT s.user_id, s.similarity
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
                    (vec, threshold, job_id, limit),
                )
                rows = cur.fetchall()
        return [
            MatchCandidate(
                user_id=row["user_id"],
                job_id=job_id,
                match_score=float(row["similarity"]),
            )
            for row in rows
        ]
