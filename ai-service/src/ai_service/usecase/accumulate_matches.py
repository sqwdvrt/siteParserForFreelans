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
        rows = [
            (
                candidate.user_id,
                candidate.job_id,
                candidate.match_score,
                candidate.raw_similarity if candidate.raw_similarity > 0 else candidate.match_score,
                candidate.final_score if candidate.final_score > 0 else candidate.match_score,
                getattr(candidate, "ranker_version", ""),
                list(getattr(candidate, "reason_codes", []) or []),
                candidate.trace_id,
            )
            for candidate in matches
        ]
        if not rows:
            return
        try:
            self._repo.upsert_many(rows)
        except Exception:  # noqa: BLE001
            logger.exception("failed to store pending matches batch size=%d", len(rows))
            raise
