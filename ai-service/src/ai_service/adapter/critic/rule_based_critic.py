"""Rule-based Critic fallback implementation."""

from __future__ import annotations

from ai_service.domain.critic_result import CriticResult
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.critic import CriticAgent


class RuleBasedCriticAgent(CriticAgent):
    """Fallback Critic with deterministic score from selection length."""

    def evaluate(self, user: User, selection: list[RankedJob]) -> CriticResult:
        del user
        score = min(float(len(selection)) * 2.0, 10.0)
        critique = (
            "Selection contains enough relevant options."
            if score >= 5.0
            else "Too few options, try broader criteria."
        )
        return CriticResult(score=score, critique=critique)
