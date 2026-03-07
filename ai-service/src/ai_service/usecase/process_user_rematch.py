"""ProcessUserRematchUseCase: find recent jobs matching this user → enqueue notifications."""

from __future__ import annotations

import logging

from ai_service.domain.ranked_job import RankedJob
from ai_service.port.match_notify_queue import MatchNotifyQueue
from ai_service.port.match_repository import MatchRepository
from ai_service.port.user_repository import UserRepository
from ai_service.util.feedback_adjuster import adjust_candidates

logger = logging.getLogger(__name__)


class ProcessUserRematchUseCase:
    """Обработка user-rematch: найти недавние jobs подходящие пользователю → enqueue."""

    def __init__(
        self,
        user_repo: UserRepository,
        match_repo: MatchRepository,
        match_notify_queue: MatchNotifyQueue,
        *,
        similarity_threshold: float = 0.7,
        max_jobs: int = 5,
        days_back: int = 7,
        feedback_repo=None,
    ) -> None:
        self._user_repo = user_repo
        self._match_repo = match_repo
        self._match_notify_queue = match_notify_queue
        self._threshold = similarity_threshold
        self._max_jobs = max_jobs
        self._days_back = days_back
        self._feedback_repo = feedback_repo

    def execute(self, user_id: int) -> int:
        """Find recent jobs matching this user and enqueue for notification.

        Returns number of matches enqueued.
        """
        embedding = self._user_repo.get_embedding(user_id)
        if embedding is None:
            logger.info("rematch: no embedding for user_id=%s, skip", user_id)
            return 0

        candidates = self._match_repo.find_jobs_for_user(
            embedding, user_id, self._threshold, self._max_jobs, self._days_back
        )
        logger.info("rematch: user_id=%s found %d job matches", user_id, len(candidates))

        if self._feedback_repo is not None and candidates:
            # For rematch direction (user→jobs) we don't resolve job skills inline
            # to avoid an extra DB round-trip per job — the adjuster uses the
            # global feedback signal only (job_skills=[]).
            candidates = adjust_candidates(
                candidates,
                get_job_skills=lambda _job_id: [],
                feedback_repo=self._feedback_repo,
                threshold=self._threshold,
            )
            logger.info(
                "rematch: user_id=%s → %d candidates after feedback adjustment",
                user_id,
                len(candidates),
            )

        enqueue_batch = getattr(self._match_notify_queue, "enqueue_batch", None)
        if len(candidates) > 1 and callable(enqueue_batch):
            ranked_jobs = [
                RankedJob(
                    job_id=c.job_id,
                    title="",
                    why_it_fits=c.why_it_fits,
                    rank=idx + 1,
                    actor_confidence=max(0.0, min(1.0, c.match_score)),
                    final_score=c.final_score if c.final_score > 0 else c.match_score,
                )
                for idx, c in enumerate(candidates)
            ]
            avg_score = sum(item.final_score for item in ranked_jobs) / len(ranked_jobs)
            enqueue_batch(
                user_id=user_id,
                ranked_jobs=ranked_jobs,
                critic_score=round(avg_score * 10, 2),
            )
            return len(candidates)

        for c in candidates:
            self._match_notify_queue.enqueue(c)

        return len(candidates)
