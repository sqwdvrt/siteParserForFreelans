"""Port: PendingJobsRepository for Actor-Critic pending matches."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PendingJobsRepository(ABC):
    """Repository for pending_ac_jobs table."""

    @abstractmethod
    def upsert(self, user_id: int, job_id: int, match_score: float, trace_id: str = "") -> None:
        """Insert or update pending match."""
        ...

    @abstractmethod
    def mark_processed(self, user_id: int, job_ids: list[int]) -> None:
        """Mark selected pending jobs as processed."""
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
    ) -> list[int]:
        """Atomically mark up to *limit* unprocessed jobs as queued and return their IDs.

        Uses SELECT … FOR UPDATE SKIP LOCKED so concurrent scheduler instances
        cannot claim the same rows.  Rows whose queued_at is older than
        *lease_timeout_sec* are eligible for re-claiming (crash-recovery path).
        """
        ...
