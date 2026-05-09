"""Port: repository for user-job filter events (explainability counters)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class FilterEventRepository(ABC):
    """Persist user/job filtering events with explicit reasons."""

    @abstractmethod
    def record_events(
        self,
        *,
        job_id: int,
        user_ids: list[int],
        reason: str,
        trace_id: str = "",
    ) -> None:
        """Store unique (user_id, job_id, reason) events."""
        ...

