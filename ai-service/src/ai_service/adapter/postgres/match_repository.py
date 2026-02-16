"""PostgreSQL implementation of MatchRepository."""

from __future__ import annotations

import contextlib

import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector
from pgvector import Vector

from ai_service.port.match_repository import MatchCandidate, MatchRepository


class PostgresMatchRepository(MatchRepository):
    """MatchRepository через PostgreSQL + pgvector."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextlib.contextmanager
    def _conn(self):
        conn = psycopg2.connect(self._dsn)
        register_vector(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_users_for_job(
        self,
        embedding: list[float],
        job_id: int,
        threshold: float,
        limit: int = 100,
    ) -> list[MatchCandidate]:
        """
        SQL: 1 - (embedding <=> $1) as score.
        WHERE embedding IS NOT NULL, score >= threshold.
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
                    SELECT u.id AS user_id, 1 - (u.embedding <=> %s) AS similarity
                    FROM users u
                    WHERE u.embedding IS NOT NULL
                      AND 1 - (u.embedding <=> %s) >= %s
                      AND u.id NOT IN (
                          SELECT user_id FROM notifications WHERE job_id = %s
                      )
                    ORDER BY similarity DESC
                    LIMIT %s
                    """,
                    (vec, vec, threshold, job_id, limit),
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
