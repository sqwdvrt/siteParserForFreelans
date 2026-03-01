"""Fallback wrapper for Critic agents."""

from __future__ import annotations

import logging

from ai_service.domain.critic_result import CriticResult
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.critic import CriticAgent
from ai_service.util import fallback_metrics

logger = logging.getLogger(__name__)


class FallbackCriticAgent(CriticAgent):
    """Use primary Critic; fallback when primary raises."""

    def __init__(self, primary: CriticAgent, fallback: CriticAgent) -> None:
        self._primary = primary
        self._fallback = fallback

    def evaluate(self, user: User, selection: list[RankedJob]) -> CriticResult:
        try:
            result = self._primary.evaluate(user=user, selection=selection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("primary critic failed: %s", exc)
            fallback_metrics.record_primary_outcome("critic", "error")
            fallback_metrics.record_fallback("critic", "primary_error")
            return self._fallback.evaluate(user=user, selection=selection)
        fallback_metrics.record_primary_outcome("critic", "success")
        return result
