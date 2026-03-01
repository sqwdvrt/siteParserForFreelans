"""Use case: process one Actor-Critic batch for a single user."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.pending_jobs_repository import PendingJobsRepository
from ai_service.usecase.actor_critic_loop import ActorCriticLoop
from ai_service.util.trace_context import get_trace_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ACBatch:
    """Batch of pending jobs for one user."""

    user_id: int
    job_ids: list[int]


class ProcessACBatchUseCase:
    """Loads user/jobs, runs Actor-Critic, and emits batch notification."""

    def __init__(
        self,
        user_repo: object,
        job_repo: object,
        pending_repo: PendingJobsRepository,
        ac_loop: ActorCriticLoop,
        notify_queue: object,
    ) -> None:
        self._user_repo = user_repo
        self._job_repo = job_repo
        self._pending_repo = pending_repo
        self._ac_loop = ac_loop
        self._notify_queue = notify_queue

    def execute(self, batch: ACBatch) -> None:
        user = self._load_user(batch.user_id)
        if user is None or not user.profile_text:
            logger.warning("skip ac batch: no profile for user_id=%d", batch.user_id)
            self._pending_repo.mark_processed(batch.user_id, batch.job_ids)
            return
        if not user.embedding or len(user.embedding) == 0:
            logger.warning("skip ac batch: no embedding for user_id=%d", batch.user_id)
            self._pending_repo.mark_processed(batch.user_id, batch.job_ids)
            return

        jobs_with_scores = self._load_jobs_with_scores(batch.job_ids, user.embedding)
        if not jobs_with_scores:
            self._pending_repo.mark_processed(batch.user_id, batch.job_ids)
            return

        result = self._ac_loop.run(user, jobs_with_scores)
        logger.info(
            "ac_batch user_id=%d jobs=%d score=%.1f passed=%s attempts=%d",
            batch.user_id,
            len(jobs_with_scores),
            result.final_score,
            result.passed,
            result.attempts,
        )

        if result.passed and result.selection:
            self._enqueue_batch_notification(
                user_id=batch.user_id,
                ranked_jobs=result.selection,
                critic_score=result.final_score,
            )

        self._pending_repo.mark_processed(batch.user_id, batch.job_ids)

    def _load_user(self, user_id: int) -> User | None:
        get_by_id = getattr(self._user_repo, "get_by_id", None)
        if callable(get_by_id):
            user = get_by_id(user_id)
            if isinstance(user, User):
                return user
            return user
        return None

    def _load_jobs_with_scores(
        self,
        job_ids: list[int],
        user_embedding: list[float],
    ) -> list[tuple]:
        get_with_scores = getattr(self._job_repo, "get_with_scores", None)
        if callable(get_with_scores):
            value = get_with_scores(job_ids, user_embedding)
            if isinstance(value, list):
                return value
        return []

    def _enqueue_batch_notification(
        self,
        *,
        user_id: int,
        ranked_jobs: list[RankedJob],
        critic_score: float,
    ) -> None:
        enqueue_batch = getattr(self._notify_queue, "enqueue_batch", None)
        if callable(enqueue_batch):
            enqueue_batch(
                user_id=user_id,
                ranked_jobs=ranked_jobs,
                critic_score=critic_score,
                trace_id=get_trace_id(),
            )
