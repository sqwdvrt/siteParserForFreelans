"""PostgreSQL implementation of JobRepository."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector

from ai_service.domain.job import Job
from ai_service.port.repository import JobRepository


class PostgresJobRepository(JobRepository):
    """JobRepository через PostgreSQL."""

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
