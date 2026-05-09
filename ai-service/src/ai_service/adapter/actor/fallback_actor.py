"""Fallback wrapper for Actor agents."""

from __future__ import annotations

import logging

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.actor import ActorAgent
from ai_service.util import fallback_metrics

logger = logging.getLogger(__name__)


class FallbackActorAgent(ActorAgent):
    """Use primary Actor; fallback when primary fails or returns empty."""

    def __init__(self, primary: ActorAgent, fallback: ActorAgent) -> None:
        self._primary = primary
        self._fallback = fallback

    def select(
        self,
        user: User,
        candidates: list[Job],
        max_jobs: int = 5,
        critique: str | None = None,
    ) -> list[RankedJob]:
        try:
            selection = self._primary.select(
                user=user,
                candidates=candidates,
                max_jobs=max_jobs,
                critique=critique,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("primary actor failed: %s", exc)
            fallback_metrics.record_primary_outcome("actor", "error")
            fallback_metrics.record_fallback("actor", "primary_error")
            return self._fallback.select(
                user=user,
                candidates=candidates,
                max_jobs=max_jobs,
                critique=critique,
            )

        if selection:
            fallback_metrics.record_primary_outcome("actor", "success")
            return selection
        fallback_metrics.record_primary_outcome("actor", "empty")
        fallback_metrics.record_fallback("actor", "primary_empty")

        return self._fallback.select(
            user=user,
            candidates=candidates,
            max_jobs=max_jobs,
            critique=critique,
        )

    def explain_batch(
        self,
        user: User,
        candidates: list[Job],
    ) -> list[str]:
        try:
            explanations = self._primary.explain_batch(
                user=user,
                candidates=candidates,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("primary actor failed during explain_batch: %s", exc)
            fallback_metrics.record_primary_outcome("actor", "error")
            fallback_metrics.record_fallback("actor", "primary_error")
            return self._fallback.explain_batch(
                user=user,
                candidates=candidates,
            )

        if len(explanations) == len(candidates) and all(str(item).strip() for item in explanations):
            fallback_metrics.record_primary_outcome("actor", "success")
            return explanations
        fallback_metrics.record_primary_outcome("actor", "empty")
        fallback_metrics.record_fallback("actor", "primary_empty")

        return self._fallback.explain_batch(
            user=user,
            candidates=candidates,
        )
