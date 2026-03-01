"""PostgreSQL implementation of PendingJobsRepository."""

from __future__ import annotations

from psycopg2.extras import RealDictCursor, execute_values

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
        normalized_trace = (trace_id or "").strip()[:128]
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

    def upsert_many(self, rows: list[tuple[int, int, float, str]]) -> None:
        if not rows:
            return

        merged = self._merge_rows(rows)
        values = [
            (user_id, job_id, match_score, trace_id)
            for (user_id, job_id), (match_score, trace_id) in merged.items()
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO pending_ac_jobs (user_id, job_id, match_score, trace_id)
                    VALUES %s
                    ON CONFLICT (user_id, job_id) DO UPDATE
                    SET
                        match_score = GREATEST(pending_ac_jobs.match_score, EXCLUDED.match_score),
                        trace_id = COALESCE(EXCLUDED.trace_id, pending_ac_jobs.trace_id),
                        created_at = NOW(),
                        processed_at = NULL,
                        queued_at = NULL
                    """,
                    values,
                    template="(%s, %s, %s, NULLIF(%s, ''))",
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
        min_jobs: int = 1,
    ) -> list[int]:
        job_ids, _trace_id = self.claim_unprocessed_job_ids_with_trace(
            user_id=user_id,
            limit=limit,
            lease_timeout_sec=lease_timeout_sec,
            min_jobs=min_jobs,
        )
        return job_ids

    def claim_unprocessed_job_ids_with_trace(
        self,
        user_id: int,
        limit: int = 100,
        lease_timeout_sec: int = 600,
        min_jobs: int = 1,
    ) -> tuple[list[int], str]:
        if limit <= 0:
            return [], ""
        min_jobs = max(1, min_jobs)
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    WITH to_claim AS (
                        SELECT id, COALESCE(trace_id, '') AS trace_id
                        FROM pending_ac_jobs
                        WHERE user_id = %s
                          AND processed_at IS NULL
                          AND (queued_at IS NULL
                               OR queued_at < NOW() - make_interval(secs => %s))
                        ORDER BY created_at
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    ),
                    eligible AS (
                        SELECT COUNT(*)::int AS cnt
                        FROM to_claim
                    )
                    UPDATE pending_ac_jobs p
                    SET queued_at = NOW()
                    FROM to_claim, eligible
                    WHERE p.id = to_claim.id
                      AND eligible.cnt >= %s
                    RETURNING p.job_id, to_claim.trace_id
                    """,
                    (user_id, lease_timeout_sec, limit, min_jobs),
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

    @staticmethod
    def _merge_rows(rows: list[tuple[int, int, float, str]]) -> dict[tuple[int, int], tuple[float, str]]:
        merged: dict[tuple[int, int], tuple[float, str]] = {}
        for user_id, job_id, match_score, trace_id in rows:
            key = (int(user_id), int(job_id))
            score = float(match_score)
            normalized_trace = str(trace_id or "").strip()[:128]
            current = merged.get(key)
            if current is None:
                merged[key] = (score, normalized_trace)
                continue
            current_score, current_trace = current
            # Preserve highest score and latest non-empty trace for duplicate keys.
            merged[key] = (max(current_score, score), normalized_trace or current_trace)
        return merged
