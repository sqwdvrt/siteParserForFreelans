"""Fallback wrapper for Critic agents."""

from __future__ import annotations

import logging

from ai_service.domain.critic_result import CriticResult
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.critic import CriticAgent
from ai_service.util import fallback_metrics

logger = logging.getLogger(__name__)


def _is_unusable_result(result: CriticResult) -> bool:
    critique = (result.critique or "").strip().lower()
    return result.score <= 0.0 and critique in {"parse error.", "circuit breaker open."}


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
        if _is_unusable_result(result):
            logger.warning("primary critic returned unusable result, using fallback")
            fallback_metrics.record_primary_outcome("critic", "invalid")
            fallback_metrics.record_fallback("critic", "primary_invalid")
            return self._fallback.evaluate(user=user, selection=selection)
        fallback_metrics.record_primary_outcome("critic", "success")
        return result
