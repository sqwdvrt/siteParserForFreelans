"""Use case: persist match candidates into pending_ac_jobs."""

from __future__ import annotations

import logging

from ai_service.port.match_repository import MatchCandidate
from ai_service.port.pending_jobs_repository import PendingJobsRepository

logger = logging.getLogger(__name__)


class AccumulateMatchesUseCase:
    """Stores match candidates for later Actor-Critic batch processing."""

    def __init__(self, pending_repo: PendingJobsRepository) -> None:
        self._repo = pending_repo

    def execute(self, matches: list[MatchCandidate]) -> None:
        for candidate in matches:
            try:
                self._repo.upsert(
                    user_id=candidate.user_id,
                    job_id=candidate.job_id,
                    match_score=candidate.match_score,
                    trace_id=candidate.trace_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "failed to store pending match user=%d job=%d",
                    candidate.user_id,
                    candidate.job_id,
                )
                raise
