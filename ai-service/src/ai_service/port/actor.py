"""Port: ActorAgent - selects best jobs for a user from candidates."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User


class ActorAgent(ABC):
    """Actor interface for job selection."""

    @abstractmethod
    def select(
        self,
        user: User,
        candidates: list[Job],
        max_jobs: int = 5,
        critique: str | None = None,
    ) -> list[RankedJob]:
        """Select top-N jobs for the user based on profile and critique."""
        ...
