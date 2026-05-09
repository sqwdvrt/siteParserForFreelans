"""PostgreSQL implementation for user-job filter events."""

from __future__ import annotations

from psycopg2.extras import execute_values

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.port.filter_event_repository import FilterEventRepository


class PostgresFilterEventRepository(PooledPostgresRepository, FilterEventRepository):
    """Persist filter events in user_job_filter_events table."""

    def __init__(
        self,
        dsn: str,
        *,
        minconn: int = 1,
        maxconn: int = 10,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        super().__init__(dsn, minconn=minconn, maxconn=maxconn, statement_timeout_ms=statement_timeout_ms)

    def record_events(
        self,
        *,
        job_id: int,
        user_ids: list[int],
        reason: str,
        trace_id: str = "",
    ) -> None:
        normalized_reason = str(reason or "").strip().lower()[:64]
        if not normalized_reason:
            return
        normalized_trace = str(trace_id or "").strip()[:128]
        normalized_user_ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
        if not normalized_user_ids:
            return
        with self._conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO user_job_filter_events (
                        user_id,
                        job_id,
                        reason,
                        trace_id
                    )
                    VALUES %s
                    ON CONFLICT (user_id, job_id, reason) DO NOTHING
                    """,
                    [
                        (
                            user_id,
                            int(job_id),
                            normalized_reason,
                            normalized_trace,
                        )
                        for user_id in normalized_user_ids
                    ],
                    template="(%s, %s, %s, NULLIF(%s, ''))",
                )

