"""Actor-Critic orchestration loop for batch recommendation quality."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ai_service.domain.ac_result import ActorCriticResult
from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.actor import ActorAgent
from ai_service.port.critic import CriticAgent

logger = logging.getLogger(__name__)


@dataclass
class ActorCriticConfig:
    """Runtime configuration for Actor-Critic loop."""

    score_threshold: float = 5.0
    max_attempts: int = 3
    max_jobs_per_selection: int = 5


class ActorCriticLoop:
    """Runs Actor-Critic iterations until threshold or attempts exhaustion."""

    def __init__(
        self,
        actor: ActorAgent,
        critic: CriticAgent,
        config: ActorCriticConfig | None = None,
    ) -> None:
        self._actor = actor
        self._critic = critic
        self._config = config or ActorCriticConfig()

    def run(self, user: User, candidates: list[tuple[Job, float]]) -> ActorCriticResult:
        """Run Actor-Critic loop for user and job candidates."""
        if not candidates:
            return ActorCriticResult(selection=[], final_score=0.0, attempts=0, passed=False)

        jobs = [job for job, _ in candidates]
        best_selection: list[RankedJob] = []
        best_score = 0.0
        critique: str | None = None
        attempts_used = 0

        for attempt in range(1, self._config.max_attempts + 1):
            attempts_used = attempt
            selection = self._actor.select(
                user=user,
                candidates=jobs,
                max_jobs=self._config.max_jobs_per_selection,
                critique=critique,
            )
            if not selection:
                logger.warning("actor returned empty selection (attempt=%d, user_id=%d)", attempt, user.id)
                break

            critic_result = self._critic.evaluate(user=user, selection=selection)
            logger.info(
                "ac_loop attempt=%d user_id=%d score=%.1f threshold=%.1f",
                attempt,
                user.id,
                critic_result.score,
                self._config.score_threshold,
            )

            if not best_selection or critic_result.score > best_score:
                best_score = critic_result.score
                best_selection = selection

            if critic_result.score >= self._config.score_threshold:
                return ActorCriticResult(
                    selection=selection,
                    final_score=critic_result.score,
                    attempts=attempt,
                    passed=True,
                )

            critique = critic_result.critique

        return ActorCriticResult(
            selection=best_selection,
            final_score=best_score,
            attempts=attempts_used,
            passed=False,
        )
