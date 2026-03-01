"""Fallback wrapper for Actor agents."""

from __future__ import annotations

import logging

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.actor import ActorAgent

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
            selection = []

        if selection:
            return selection

        return self._fallback.select(
            user=user,
            candidates=candidates,
            max_jobs=max_jobs,
            critique=critique,
        )
