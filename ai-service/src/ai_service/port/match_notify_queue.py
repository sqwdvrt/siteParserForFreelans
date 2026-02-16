"""Port: MatchNotifyQueue — очередь кандидатов для уведомлений (user_id, job_id, match_score)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_service.port.match_repository import MatchCandidate


class MatchNotifyQueue(ABC):
    """Очередь для передачи кандидатов в Backend (match-notify)."""

    @abstractmethod
    def enqueue(self, candidate: MatchCandidate) -> None:
        """Добавить кандидата в очередь."""
        ...
