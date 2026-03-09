"""PostgreSQL implementation of JobRepository."""

from __future__ import annotations

import json
from typing import Any

from pgvector import Vector
from psycopg2.extras import RealDictCursor

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.domain.job import Job
from ai_service.port.repository import JobEmbeddingRecord, JobRepository


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
                    """
                    SELECT
                        id, source, url, title, description, COALESCE(budget, '') AS budget,
                        raw_html, posted_at, created_at
                    FROM jobs
                    WHERE id = %s
                    """,
                    (job_id,),
                )
                row: dict[str, Any] | None = cur.fetchone()
        if row is None:
            return None
        return Job(
            id=row["id"],
            source=row["source"] or "kwork",
            url=row["url"] or "",
            title=row["title"] or "",
            description=row["description"],
            budget=row["budget"] or "",
            raw_html=row["raw_html"] or "",
            posted_at=row["posted_at"],
            created_at=row["created_at"],
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

    def get_embedding(self, job_id: int) -> JobEmbeddingRecord | None:
        """Load saved embedding and ai_metadata for retry-safe reprocessing."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT embedding, COALESCE(ai_metadata, '{}'::jsonb) AS ai_metadata
                    FROM job_embeddings
                    WHERE job_id = %s
                    LIMIT 1
                    """,
                    (job_id,),
                )
                row: dict[str, Any] | None = cur.fetchone()
        if row is None:
            return None
        metadata = row["ai_metadata"] if isinstance(row["ai_metadata"], dict) else {}
        raw_embedding = row["embedding"]
        return JobEmbeddingRecord(
            embedding=list(raw_embedding) if raw_embedding is not None else [],
            metadata=metadata,
        )

    def has_recent_similar_title(self, job_id: int, title: str, days: int = 7) -> bool:
        normalized_title = " ".join((title or "").strip().lower().split())
        if not normalized_title:
            return False
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM jobs j
                        WHERE j.id <> %s
                          AND j.created_at >= NOW() - make_interval(days => %s)
                          AND regexp_replace(
                                regexp_replace(lower(COALESCE(j.title, '')), '[^[:alnum:][:space:]]+', ' ', 'g'),
                                '\\s+',
                                ' ',
                                'g'
                              ) = regexp_replace(
                                regexp_replace(lower(%s), '[^[:alnum:][:space:]]+', ' ', 'g'),
                                '\\s+',
                                ' ',
                                'g'
                              )
                    )
                    """,
                    (job_id, days, normalized_title),
                )
                row = cur.fetchone()
        return bool(row[0]) if row else False

    def get_with_scores(
        self,
        job_ids: list[int],
        user_embedding: list[float],
        user_id: int | None = None,
    ) -> list[tuple[Job, float]]:
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
                        j.source,
                        j.url,
                        j.title,
                        j.description,
                        COALESCE(j.budget, '') AS budget,
                        j.raw_html,
                        j.posted_at,
                        j.created_at,
                        ARRAY(
                            SELECT lower(trim(tag.value))
                            FROM jsonb_array_elements_text(
                                COALESCE(je.ai_metadata->'classification'->'technologies', '[]'::jsonb)
                            ) AS tag(value)
                            WHERE trim(tag.value) <> ''
                        ) AS technologies,
                        1 - (je.embedding <=> %s) AS similarity,
                        COALESCE(p.match_score, 1 - (je.embedding <=> %s)) AS match_score,
                        COALESCE(p.raw_similarity, 1 - (je.embedding <=> %s)) AS raw_similarity,
                        COALESCE(p.final_score, COALESCE(p.match_score, 1 - (je.embedding <=> %s))) AS final_score,
                        COALESCE(p.ranker_version, '') AS ranker_version,
                        COALESCE(p.reason_codes, ARRAY[]::text[]) AS reason_codes
                    FROM jobs j
                    JOIN job_embeddings je ON je.job_id = j.id
                    LEFT JOIN pending_ac_jobs p
                      ON p.job_id = j.id
                     AND (%s IS NULL OR p.user_id = %s)
                    WHERE j.id = ANY(%s)
                    ORDER BY COALESCE(p.final_score, p.match_score, 1 - (je.embedding <=> %s)) DESC, similarity DESC
                    """,
                    (
                        Vector(user_embedding),
                        Vector(user_embedding),
                        Vector(user_embedding),
                        Vector(user_embedding),
                        user_id,
                        user_id,
                        job_ids,
                        Vector(user_embedding),
                    ),
                )
                rows: list[dict[str, Any]] = cur.fetchall()

        result: list[tuple[Job, float]] = []
        for row in rows:
            result.append(
                (
                    Job(
                        id=row["id"],
                        source=row["source"] or "kwork",
                        url=row["url"] or "",
                        title=row["title"] or "",
                        description=row["description"],
                        budget=row["budget"] or "",
                        raw_html=row["raw_html"] or "",
                        technologies=list(row["technologies"] or []),
                        posted_at=row["posted_at"],
                        created_at=row["created_at"],
                        match_score=float(row["match_score"]),
                        raw_similarity=float(row["raw_similarity"]),
                        final_score=float(row["final_score"]),
                        ranker_version=row["ranker_version"] or "",
                        reason_codes=list(row["reason_codes"] or []),
                    ),
                    float(row["similarity"]),
                )
            )
        return result
