"""Port: MatchNotifyQueue — очередь кандидатов для уведомлений (user_id, job_id, match_score)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_service.domain.ranked_job import RankedJob
from ai_service.port.match_repository import MatchCandidate


class MatchNotifyQueue(ABC):
    """Очередь для передачи кандидатов в Backend (match-notify)."""

    @abstractmethod
    def enqueue(self, candidate: MatchCandidate) -> None:
        """Добавить кандидата в очередь."""
        ...

    def enqueue_many(self, candidates: list[MatchCandidate]) -> None:
        """Добавить несколько кандидатов в очередь (по умолчанию через enqueue)."""
        for candidate in candidates:
            self.enqueue(candidate)

    def enqueue_batch(
        self,
        *,
        user_id: int,
        ranked_jobs: list[RankedJob],
        source: str,
        batch_score: float | None = None,
        critic_score: float | None = None,
        trace_id: str = "",
    ) -> None:
        """Добавить один batch кандидатов в очередь."""
        raise NotImplementedError
