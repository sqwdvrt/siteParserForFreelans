"""PostgreSQL implementation of JobRepository."""

from __future__ import annotations

import json
from typing import Any

from pgvector import Vector
from psycopg2.extras import RealDictCursor

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.domain.job import Job
from ai_service.port.repository import JobRepository


class PostgresJobRepository(PooledPostgresRepository, JobRepository):
    """JobRepository через PostgreSQL."""

    def __init__(
        self,
        dsn: str,
        *,
        minconn: int = 1,
        maxconn: int = 10,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        super().__init__(dsn, minconn=minconn, maxconn=maxconn, statement_timeout_ms=statement_timeout_ms)

    def get(self, job_id: int) -> Job | None:
        """Получить job по id."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, title, description, raw_html FROM jobs WHERE id = %s",
                    (job_id,),
                )
                row: dict[str, Any] | None = cur.fetchone()
        if row is None:
            return None
        return Job(
            id=row["id"],
            title=row["title"] or "",
            description=row["description"],
            raw_html=row["raw_html"] or "",
        )

    def save_embedding(
        self,
        job_id: int,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        """Сохранить embedding в job_embeddings."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO job_embeddings (job_id, embedding, ai_metadata)
                    VALUES (%s, %s, %s::jsonb)
                    ON CONFLICT (job_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        ai_metadata = EXCLUDED.ai_metadata
                    """,
                    (job_id, embedding, json.dumps(metadata)),
                )

    def has_embedding(self, job_id: int) -> bool:
        """Проверить наличие embedding для job_id."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM job_embeddings WHERE job_id = %s LIMIT 1",
                    (job_id,),
                )
                return cur.fetchone() is not None

    def get_with_scores(self, job_ids: list[int], user_embedding: list[float]) -> list[tuple[Job, float]]:
        """Load jobs by ids with similarity score against user embedding."""
        if not job_ids:
            return []
        if user_embedding is None or len(user_embedding) == 0 or len(user_embedding) != 384:
            return []

        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        j.id,
                        j.title,
                        j.description,
                        j.raw_html,
                        1 - (je.embedding <=> %s) AS similarity
                    FROM jobs j
                    JOIN job_embeddings je ON je.job_id = j.id
                    WHERE j.id = ANY(%s)
                    ORDER BY similarity DESC
                    """,
                    (Vector(user_embedding), job_ids),
                )
                rows: list[dict[str, Any]] = cur.fetchall()

        result: list[tuple[Job, float]] = []
        for row in rows:
            result.append(
                (
                    Job(
                        id=row["id"],
                        title=row["title"] or "",
                        description=row["description"],
                        raw_html=row["raw_html"] or "",
                    ),
                    float(row["similarity"]),
                )
            )
        return result
