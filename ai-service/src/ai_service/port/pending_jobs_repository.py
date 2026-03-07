"""Port: PendingJobsRepository for Actor-Critic pending matches."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PendingJobsRepository(ABC):
    """Repository for pending_ac_jobs table."""

    @abstractmethod
    def upsert(
        self,
        user_id: int,
        job_id: int,
        match_score: float,
        *,
        raw_similarity: float = 0.0,
        final_score: float = 0.0,
        ranker_version: str = "",
        reason_codes: list[str] | None = None,
        trace_id: str = "",
    ) -> None:
        """Insert or update pending match."""
        ...

    def upsert_many(
        self,
        rows: list[tuple[int, int, float, float, float, str, list[str] | None, str]],
    ) -> None:
        """Insert or update multiple pending matches."""
        for user_id, job_id, match_score, raw_similarity, final_score, ranker_version, reason_codes, trace_id in rows:
            self.upsert(
                user_id=user_id,
                job_id=job_id,
                match_score=match_score,
                raw_similarity=raw_similarity,
                final_score=final_score,
                ranker_version=ranker_version,
                reason_codes=reason_codes,
                trace_id=trace_id,
            )

    @abstractmethod
    def mark_processed(self, user_id: int, job_ids: list[int]) -> None:
        """Mark selected pending jobs as processed."""
        ...

    @abstractmethod
    def release_claim(self, user_id: int, job_ids: list[int]) -> None:
        """Release queued-but-unprocessed jobs back to pending state."""
        ...

    @abstractmethod
    def list_unprocessed_user_ids(self) -> list[int]:
        """Return distinct user IDs with unprocessed pending jobs."""
        ...

    @abstractmethod
    def list_unprocessed_job_ids(self, user_id: int, limit: int = 100) -> list[int]:
        """Return unprocessed job IDs for user ordered by created_at."""
        ...

    @abstractmethod
    def list_unprocessed_job_ids_with_trace(self, user_id: int, limit: int = 100) -> tuple[list[int], str]:
        """Return (job_ids, trace_id) for pending batch; trace_id may be empty."""
        ...

    @abstractmethod
    def claim_unprocessed_job_ids(
        self,
        user_id: int,
        limit: int = 100,
        lease_timeout_sec: int = 600,
        min_jobs: int = 1,
    ) -> list[int]:
        """Atomically mark up to *limit* unprocessed jobs as queued and return their IDs.

        Uses SELECT … FOR UPDATE SKIP LOCKED so concurrent scheduler instances
        cannot claim the same rows.  Rows whose queued_at is older than
        *lease_timeout_sec* are eligible for re-claiming (crash-recovery path).
        Claim succeeds only when at least *min_jobs* rows are selected; otherwise
        returns empty list and leaves queued_at unchanged.
        """
        ...

    @abstractmethod
    def claim_unprocessed_job_ids_with_trace(
        self,
        user_id: int,
        limit: int = 100,
        lease_timeout_sec: int = 600,
        min_jobs: int = 1,
    ) -> tuple[list[int], str]:
        """Claim pending jobs and return (job_ids, trace_id) for one batch.

        Returned trace_id may be empty when none of claimed rows has trace_id.
        If selected rows are fewer than *min_jobs*, returns empty result and
        keeps rows unqueued.
        """
        ...
