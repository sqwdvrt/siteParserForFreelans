"""ProcessUserRematchUseCase: find recent jobs matching this user → enqueue notifications."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ai_service.domain.ranked_job import RankedJob
from ai_service.port.match_notify_queue import MatchNotifyQueue
from ai_service.port.match_repository import MatchRepository
from ai_service.port.repository import JobRepository
from ai_service.port.user_repository import UserRepository
from ai_service.util.feedback_adjuster import adjust_candidates
from ai_service.util.preference_filter import evaluate_preference_filter

if TYPE_CHECKING:
    from ai_service.port.classifier import ClassificationResult
    from ai_service.port.match_repository import MatchCandidate

logger = logging.getLogger(__name__)
DEFAULT_REMATCH_CANDIDATE_POOL = 50


class ProcessUserRematchUseCase:
    """Обработка user-rematch: найти недавние jobs подходящие пользователю → enqueue."""

    def __init__(
        self,
        user_repo: UserRepository,
        match_repo: MatchRepository,
        match_notify_queue: MatchNotifyQueue,
        *,
        job_repo: JobRepository,
        similarity_threshold: float = 0.7,
        max_jobs: int = 5,
        days_back: int = 7,
        feedback_repo=None,
    ) -> None:
        self._user_repo = user_repo
        self._match_repo = match_repo
        self._match_notify_queue = match_notify_queue
        self._job_repo = job_repo
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

        candidate_limit = max(self._max_jobs, DEFAULT_REMATCH_CANDIDATE_POOL)
        candidates = self._match_repo.find_jobs_for_user(
            embedding, user_id, self._threshold, candidate_limit, self._days_back
        )
        logger.info("rematch: user_id=%s found %d job matches", user_id, len(candidates))
        candidates = self._apply_preference_filter(user_id, candidates)

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

        if len(candidates) > self._max_jobs:
            candidates = candidates[: self._max_jobs]

        enqueue_batch = getattr(self._match_notify_queue, "enqueue_batch", None)
        if callable(enqueue_batch):
            ranked_jobs = self._build_ranked_jobs(candidates)
            if ranked_jobs:
                enqueue_batch(
                    user_id=user_id,
                    ranked_jobs=ranked_jobs,
                    source="rematch",
                    batch_score=self._batch_score(ranked_jobs),
                )
            logger.info("rematch: user_id=%s enqueued batch jobs=%d", user_id, len(ranked_jobs))
            return len(ranked_jobs)

        enqueue_many = getattr(self._match_notify_queue, "enqueue_many", None)
        if callable(enqueue_many):
            enqueue_many(candidates)
            return len(candidates)

        for candidate in candidates:
            self._match_notify_queue.enqueue(candidate)

        return len(candidates)

    def _build_ranked_jobs(self, candidates: list[MatchCandidate]) -> list[RankedJob]:
        ranked_jobs: list[RankedJob] = []
        for candidate in candidates:
            job = self._job_repo.get(candidate.job_id)
            if job is None:
                logger.warning(
                    "rematch: skip missing job_id=%s while building batch for user_id=%s",
                    candidate.job_id,
                    candidate.user_id,
                )
                continue
            ranked_jobs.append(
                RankedJob(
                    job_id=candidate.job_id,
                    title=job.title,
                    why_it_fits=candidate.why_it_fits,
                    rank=len(ranked_jobs) + 1,
                    actor_confidence=candidate.match_score,
                    final_score=candidate.final_score if candidate.final_score > 0 else candidate.match_score,
                    ranker_version=candidate.ranker_version,
                    reason_codes=tuple(candidate.reason_codes),
                )
            )
        return ranked_jobs

    @staticmethod
    def _batch_score(ranked_jobs: list[RankedJob]) -> float:
        if not ranked_jobs:
            return 0.0
        total = 0.0
        for item in ranked_jobs:
            total += item.final_score
        return round(total / len(ranked_jobs) * 10.0, 2)

    def _apply_preference_filter(self, user_id: int, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        if not candidates:
            return []

        user = self._user_repo.get_by_id(user_id)
        if user is None:
            logger.warning("rematch: user_id=%s disappeared before preference filtering", user_id)
            return []

        filtered_candidates: list[MatchCandidate] = []
        for candidate in candidates:
            job = self._job_repo.get(candidate.job_id)
            if job is None:
                logger.warning(
                    "rematch: skip missing job_id=%s during preference filtering for user_id=%s",
                    candidate.job_id,
                    user_id,
                )
                continue
            classification = self._load_job_classification(candidate.job_id)
            passed_users = evaluate_preference_filter(job, [user], classification=classification).passed
            if passed_users:
                filtered_candidates.append(candidate)

        filtered_count = len(candidates) - len(filtered_candidates)
        if filtered_count > 0:
            logger.info(
                "rematch: user_id=%s preference filter excluded %d/%d candidates",
                user_id,
                filtered_count,
                len(candidates),
            )
        return filtered_candidates

    def _load_job_classification(self, job_id: int) -> ClassificationResult:
        job_embedding = self._job_repo.get_embedding(job_id)
        if job_embedding is None:
            return {}

        raw_classification = job_embedding.metadata.get("classification")
        if isinstance(raw_classification, dict):
            return raw_classification
        return {}
