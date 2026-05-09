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

    def upsert(
        self,
        user_id: int,
        job_id: int,
        match_score: float,
        *,
        rerank_score: float = 0.0,
        raw_similarity: float = 0.0,
        feedback_bonus: float = 0.0,
        preference_multiplier: float = 1.0,
        final_score: float = 0.0,
        ranker_version: str = "",
        reason_codes: list[str] | None = None,
        trace_id: str = "",
    ) -> None:
        normalized_trace = (trace_id or "").strip()[:128]
        normalized_reasons = [str(code).strip()[:64] for code in (reason_codes or []) if str(code).strip()]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pending_ac_jobs (
                        user_id,
                        job_id,
                        match_score,
                        rerank_score,
                        raw_similarity,
                        feedback_bonus,
                        preference_multiplier,
                        final_score,
                        ranker_version,
                        reason_codes,
                        trace_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, ''), %s, NULLIF(%s, ''))
                    ON CONFLICT (user_id, job_id) DO UPDATE
                    SET
                        match_score = GREATEST(pending_ac_jobs.match_score, EXCLUDED.match_score),
                        rerank_score = GREATEST(
                            COALESCE(pending_ac_jobs.rerank_score, 0),
                            COALESCE(EXCLUDED.rerank_score, 0)
                        ),
                        raw_similarity = GREATEST(
                            COALESCE(pending_ac_jobs.raw_similarity, 0),
                            COALESCE(EXCLUDED.raw_similarity, 0)
                        ),
                        feedback_bonus = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.feedback_bonus, pending_ac_jobs.feedback_bonus)
                            ELSE pending_ac_jobs.feedback_bonus
                        END,
                        preference_multiplier = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.preference_multiplier, pending_ac_jobs.preference_multiplier)
                            ELSE pending_ac_jobs.preference_multiplier
                        END,
                        final_score = GREATEST(
                            COALESCE(pending_ac_jobs.final_score, 0),
                            COALESCE(EXCLUDED.final_score, 0)
                        ),
                        ranker_version = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.ranker_version, pending_ac_jobs.ranker_version)
                            ELSE pending_ac_jobs.ranker_version
                        END,
                        reason_codes = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.reason_codes, pending_ac_jobs.reason_codes)
                            ELSE pending_ac_jobs.reason_codes
                        END,
                        trace_id = COALESCE(EXCLUDED.trace_id, pending_ac_jobs.trace_id),
                        created_at = NOW(),
                        processed_at = NULL,
                        queued_at = NULL
                    """,
                    (
                        user_id,
                        job_id,
                        match_score,
                        rerank_score,
                        raw_similarity,
                        feedback_bonus,
                        preference_multiplier,
                        final_score,
                        ranker_version,
                        normalized_reasons,
                        normalized_trace,
                    ),
                )

    def upsert_many(
        self,
        rows: list[
            tuple[int, int, float, float, float, str, list[str] | None, str]
            | tuple[int, int, float, float, float, float, str, list[str] | None, str]
            | tuple[int, int, float, float, float, float, float, float, str, list[str] | None, str]
        ],
    ) -> None:
        if not rows:
            return

        merged = self._merge_rows(rows)
        values = [
            (
                user_id,
                job_id,
                match_score,
                rerank_score,
                raw_similarity,
                feedback_bonus,
                preference_multiplier,
                final_score,
                ranker_version,
                reason_codes,
                trace_id,
            )
            for (user_id, job_id), (
                match_score,
                rerank_score,
                raw_similarity,
                feedback_bonus,
                preference_multiplier,
                final_score,
                ranker_version,
                reason_codes,
                trace_id,
            ) in merged.items()
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO pending_ac_jobs (
                        user_id,
                        job_id,
                        match_score,
                        rerank_score,
                        raw_similarity,
                        feedback_bonus,
                        preference_multiplier,
                        final_score,
                        ranker_version,
                        reason_codes,
                        trace_id
                    )
                    VALUES %s
                    ON CONFLICT (user_id, job_id) DO UPDATE
                    SET
                        match_score = GREATEST(pending_ac_jobs.match_score, EXCLUDED.match_score),
                        rerank_score = GREATEST(
                            COALESCE(pending_ac_jobs.rerank_score, 0),
                            COALESCE(EXCLUDED.rerank_score, 0)
                        ),
                        raw_similarity = GREATEST(
                            COALESCE(pending_ac_jobs.raw_similarity, 0),
                            COALESCE(EXCLUDED.raw_similarity, 0)
                        ),
                        feedback_bonus = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.feedback_bonus, pending_ac_jobs.feedback_bonus)
                            ELSE pending_ac_jobs.feedback_bonus
                        END,
                        preference_multiplier = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.preference_multiplier, pending_ac_jobs.preference_multiplier)
                            ELSE pending_ac_jobs.preference_multiplier
                        END,
                        final_score = GREATEST(
                            COALESCE(pending_ac_jobs.final_score, 0),
                            COALESCE(EXCLUDED.final_score, 0)
                        ),
                        ranker_version = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.ranker_version, pending_ac_jobs.ranker_version)
                            ELSE pending_ac_jobs.ranker_version
                        END,
                        reason_codes = CASE
                            WHEN COALESCE(EXCLUDED.final_score, 0) >= COALESCE(pending_ac_jobs.final_score, 0)
                                THEN COALESCE(EXCLUDED.reason_codes, pending_ac_jobs.reason_codes)
                            ELSE pending_ac_jobs.reason_codes
                        END,
                        trace_id = COALESCE(EXCLUDED.trace_id, pending_ac_jobs.trace_id),
                        created_at = NOW(),
                        processed_at = NULL,
                        queued_at = NULL
                    """,
                    values,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, ''), %s, NULLIF(%s, ''))",
                )

    def save_scoring_components(
        self,
        user_id: int,
        rows: list[tuple[int, float, float, float, float]],
    ) -> None:
        # Keep scoring components in pending rows for downstream debugging/tracing.
        if not rows:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE pending_ac_jobs
                    SET
                        rerank_score = %s,
                        feedback_bonus = %s,
                        preference_multiplier = %s,
                        final_score = %s
                    WHERE user_id = %s
                      AND job_id = %s
                    """,
                    [
                        (
                            float(rerank_score),
                            float(feedback_bonus),
                            float(preference_multiplier),
                            float(final_score),
                            int(user_id),
                            int(job_id),
                        )
                        for (job_id, rerank_score, feedback_bonus, preference_multiplier, final_score) in rows
                    ],
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

    def release_claim(self, user_id: int, job_ids: list[int]) -> None:
        if not job_ids:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pending_ac_jobs
                    SET queued_at = NULL
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

    def count_rows(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM pending_ac_jobs")
                row = cur.fetchone()
        return int(row[0] if row else 0)

    def count_unprocessed_rows(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM pending_ac_jobs WHERE processed_at IS NULL")
                row = cur.fetchone()
        return int(row[0] if row else 0)

    def delete_processed_older_than(self, older_than_days: int) -> int:
        if older_than_days <= 0:
            return 0
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM pending_ac_jobs
                    WHERE processed_at IS NOT NULL
                      AND processed_at < NOW() - make_interval(days => %s)
                    """,
                    (older_than_days,),
                )
                deleted = cur.rowcount
        return int(deleted or 0)

    @staticmethod
    def _merge_rows(
        rows: list[
            tuple[int, int, float, float, float, str, list[str] | None, str]
            | tuple[int, int, float, float, float, float, str, list[str] | None, str]
            | tuple[int, int, float, float, float, float, float, float, str, list[str] | None, str]
        ],
    ) -> dict[tuple[int, int], tuple[float, float, float, float, float, float, str, list[str], str]]:
        merged: dict[tuple[int, int], tuple[float, float, float, float, float, float, str, list[str], str]] = {}
        for row in rows:
            if len(row) == 8:
                (
                    user_id,
                    job_id,
                    match_score,
                    raw_similarity,
                    final_score,
                    ranker_version,
                    reason_codes,
                    trace_id,
                ) = row
                rerank_score = 0.0
                feedback_bonus = 0.0
                preference_multiplier = 1.0
            elif len(row) == 9:
                (
                    user_id,
                    job_id,
                    match_score,
                    rerank_score,
                    raw_similarity,
                    final_score,
                    ranker_version,
                    reason_codes,
                    trace_id,
                ) = row
                feedback_bonus = 0.0
                preference_multiplier = 1.0
            elif len(row) == 11:
                (
                    user_id,
                    job_id,
                    match_score,
                    rerank_score,
                    raw_similarity,
                    feedback_bonus,
                    preference_multiplier,
                    final_score,
                    ranker_version,
                    reason_codes,
                    trace_id,
                ) = row
            else:
                raise ValueError(f"invalid pending match row length: {len(row)}")
            key = (int(user_id), int(job_id))
            score = float(match_score)
            normalized_rerank = float(rerank_score)
            similarity = float(raw_similarity)
            normalized_feedback_bonus = float(feedback_bonus)
            normalized_preference_multiplier = float(preference_multiplier)
            normalized_final = float(final_score)
            normalized_ranker = str(ranker_version or "").strip()[:64]
            normalized_reasons = [str(code).strip()[:64] for code in (reason_codes or []) if str(code).strip()]
            normalized_trace = str(trace_id or "").strip()[:128]
            current = merged.get(key)
            if current is None:
                merged[key] = (
                    score,
                    normalized_rerank,
                    similarity,
                    normalized_feedback_bonus,
                    normalized_preference_multiplier,
                    normalized_final,
                    normalized_ranker,
                    normalized_reasons,
                    normalized_trace,
                )
                continue
            (
                current_score,
                current_rerank,
                current_similarity,
                current_feedback_bonus,
                current_preference_multiplier,
                current_final,
                current_ranker,
                current_reasons,
                current_trace,
            ) = current
            keep_new = normalized_final >= current_final
            merged[key] = (
                max(current_score, score),
                max(current_rerank, normalized_rerank),
                max(current_similarity, similarity),
                normalized_feedback_bonus if keep_new else current_feedback_bonus,
                normalized_preference_multiplier if keep_new else current_preference_multiplier,
                max(current_final, normalized_final),
                normalized_ranker if keep_new and normalized_ranker else current_ranker,
                normalized_reasons if keep_new and normalized_reasons else current_reasons,
                normalized_trace or current_trace,
            )
        return merged
