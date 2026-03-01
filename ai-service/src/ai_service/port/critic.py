"""Port: CriticAgent - evaluates quality of Actor selection."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_service.domain.critic_result import CriticResult
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User


class CriticAgent(ABC):
    """Critic interface for scoring recommendation quality."""

    @abstractmethod
    def evaluate(self, user: User, selection: list[RankedJob]) -> CriticResult:
        """Evaluate recommendation quality from 0 to 10 with critique text."""
        ...
