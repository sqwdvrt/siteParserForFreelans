"""Use case: process one Actor-Critic batch for a single user."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.pending_jobs_repository import PendingJobsRepository
from ai_service.usecase.actor_critic_loop import ActorCriticLoop
from ai_service.util.trace_context import get_trace_id

logger = logging.getLogger(__name__)
MAX_SAME_SOURCE_PER_BATCH = 2
MAX_SAME_TAG_PER_BATCH = 2


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

        jobs_with_scores = self._load_jobs_with_scores(batch.user_id, batch.job_ids, user.embedding)
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

        # Отправляем best_selection независимо от passed.
        # Critic используется для улучшения качества через retry, но не должен
        # блокировать доставку: пользователь должен получить лучшее из найденного,
        # даже если порог score не достигнут.
        if result.selection:
            diversified = self._diversify_selection(result.selection, jobs_with_scores)
            self._enqueue_batch_notification(
                user_id=batch.user_id,
                ranked_jobs=diversified,
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
        user_id: int,
        job_ids: list[int],
        user_embedding: list[float],
    ) -> list[tuple]:
        get_with_scores = getattr(self._job_repo, "get_with_scores", None)
        if callable(get_with_scores):
            value = get_with_scores(job_ids, user_embedding, user_id)
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

    def _diversify_selection(
        self,
        selection: list[RankedJob],
        jobs_with_scores: list[tuple[Job, float]],
    ) -> list[RankedJob]:
        if not selection:
            return []

        job_by_id = {job.id: job for job, _score in jobs_with_scores}
        diversified: list[RankedJob] = []
        source_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        ordered = sorted(selection, key=lambda item: (item.rank, -item.actor_confidence, item.job_id))

        for item in ordered:
            job = job_by_id.get(item.job_id)
            source = self._normalize_source(job)
            tags = self._normalize_tags(job)
            if diversified and self._hits_diversity_limit(source, tags, source_counts, tag_counts):
                logger.info("drop job_id=%d from ac batch due to diversity limits", item.job_id)
                continue
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            diversified.append(self._enrich_ranked_job(item, job, rank=len(diversified) + 1))

        if diversified:
            return diversified
        first = ordered[0]
        return [self._enrich_ranked_job(first, job_by_id.get(first.job_id), rank=1)]

    @staticmethod
    def _normalize_source(job: Job | None) -> str:
        if job is None or not job.source:
            return ""
        return " ".join(job.source.strip().lower().split())

    @staticmethod
    def _normalize_tags(job: Job | None) -> list[str]:
        if job is None or not job.technologies:
            return []
        seen: set[str] = set()
        normalized: list[str] = []
        for raw in job.technologies:
            tag = " ".join(str(raw or "").strip().lower().split())
            if not tag or tag in seen:
                continue
            seen.add(tag)
            normalized.append(tag)
        return normalized

    @staticmethod
    def _hits_diversity_limit(
        source: str,
        tags: list[str],
        source_counts: dict[str, int],
        tag_counts: dict[str, int],
    ) -> bool:
        if source and source_counts.get(source, 0) >= MAX_SAME_SOURCE_PER_BATCH:
            return True
        return any(tag_counts.get(tag, 0) >= MAX_SAME_TAG_PER_BATCH for tag in tags)

    @staticmethod
    def _enrich_ranked_job(item: RankedJob, job: Job | None, *, rank: int) -> RankedJob:
        if job is None:
            return RankedJob(
                job_id=item.job_id,
                title=item.title,
                why_it_fits=item.why_it_fits,
                rank=rank,
                actor_confidence=item.actor_confidence,
                final_score=item.final_score,
                ranker_version=item.ranker_version,
                reason_codes=item.reason_codes,
            )
        return RankedJob(
            job_id=item.job_id,
            title=item.title or job.title,
            why_it_fits=item.why_it_fits,
            rank=rank,
            actor_confidence=item.actor_confidence,
            final_score=float(job.final_score or item.final_score),
            ranker_version=job.ranker_version or item.ranker_version,
            reason_codes=tuple(job.reason_codes or item.reason_codes),
        )
