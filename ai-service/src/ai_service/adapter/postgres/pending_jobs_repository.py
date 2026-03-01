"""PostgreSQL implementation of PendingJobsRepository."""

from __future__ import annotations

from psycopg2.extras import RealDictCursor

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.port.pending_jobs_repository import PendingJobsRepository


class PostgresPendingJobsRepository(PooledPostgresRepository, PendingJobsRepository):
    """PendingJobsRepository via PostgreSQL pending_ac_jobs table."""

    def __init__(
        self,
        dsn: str,
        *,
        minconn: int = 1,
        maxconn: int = 10,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        super().__init__(dsn, minconn=minconn, maxconn=maxconn, statement_timeout_ms=statement_timeout_ms)

    def upsert(self, user_id: int, job_id: int, match_score: float, trace_id: str = "") -> None:
        normalized_trace = trace_id.strip()[:128]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pending_ac_jobs (user_id, job_id, match_score, trace_id)
                    VALUES (%s, %s, %s, NULLIF(%s, ''))
                    ON CONFLICT (user_id, job_id) DO UPDATE
                    SET
                        match_score = GREATEST(pending_ac_jobs.match_score, EXCLUDED.match_score),
                        trace_id = COALESCE(EXCLUDED.trace_id, pending_ac_jobs.trace_id),
                        created_at = NOW(),
                        processed_at = NULL,
                        queued_at = NULL
                    """,
                    (user_id, job_id, match_score, normalized_trace),
                )

    def mark_processed(self, user_id: int, job_ids: list[int]) -> None:
        if not job_ids:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pending_ac_jobs
                    SET processed_at = NOW()
                    WHERE user_id = %s
                      AND job_id = ANY(%s)
                      AND processed_at IS NULL
                    """,
                    (user_id, job_ids),
                )

    def list_unprocessed_user_ids(self, lease_timeout_sec: int = 600) -> list[int]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT user_id
                    FROM pending_ac_jobs
                    WHERE processed_at IS NULL
                      AND (queued_at IS NULL
                           OR queued_at < NOW() - make_interval(secs => %s))
                    ORDER BY user_id
                    """,
                    (lease_timeout_sec,),
                )
                rows = cur.fetchall()
        return [int(row["user_id"]) for row in rows]

    def claim_unprocessed_job_ids(
        self,
        user_id: int,
        limit: int = 100,
        lease_timeout_sec: int = 600,
    ) -> list[int]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    WITH to_claim AS (
                        SELECT id
                        FROM pending_ac_jobs
                        WHERE user_id = %s
                          AND processed_at IS NULL
                          AND (queued_at IS NULL
                               OR queued_at < NOW() - make_interval(secs => %s))
                        ORDER BY created_at
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE pending_ac_jobs p
                    SET queued_at = NOW()
                    FROM to_claim
                    WHERE p.id = to_claim.id
                    RETURNING p.job_id
                    """,
                    (user_id, lease_timeout_sec, limit),
                )
                rows = cur.fetchall()
        return [int(row["job_id"]) for row in rows]

    def list_unprocessed_job_ids(self, user_id: int, limit: int = 100) -> list[int]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT job_id
                    FROM pending_ac_jobs
                    WHERE user_id = %s
                      AND processed_at IS NULL
                    ORDER BY created_at
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
        return [int(row["job_id"]) for row in rows]

    def list_unprocessed_job_ids_with_trace(self, user_id: int, limit: int = 100) -> tuple[list[int], str]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT job_id, COALESCE(trace_id, '') AS trace_id
                    FROM pending_ac_jobs
                    WHERE user_id = %s
                      AND processed_at IS NULL
                    ORDER BY created_at
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
        job_ids = [int(row["job_id"]) for row in rows]
        trace_id = ""
        for row in rows:
            value = str(row.get("trace_id") or "").strip()
            if value:
                trace_id = value[:128]
                break
        return job_ids, trace_id
