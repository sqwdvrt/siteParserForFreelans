"""Use case: process one Actor-Critic batch for a single user."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol
from ai_service.subscription.guard import SubscriptionGuard

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.pending_jobs_repository import PendingJobsRepository
from ai_service.usecase.rerank_policy import apply_rerank_policy
from ai_service.util.feedback_adjuster import adjust_score
from ai_service.util.trace_context import get_trace_id

logger = logging.getLogger(__name__)
MAX_SAME_SOURCE_PER_BATCH = 2
MAX_SAME_TAG_PER_BATCH = 2
PREFERRED_SOURCE_MULTIPLIER = 1.2
NON_PREFERRED_SOURCE_MULTIPLIER = 0.8
TIME_DECAY_HOURS = 48.0
COMPETITION_SCALE = 50.0
TAG_AFFINITY_WEIGHT_PER_TAG = 0.05  # +5% per affinity point, capped at +15%
TAG_AFFINITY_MAX_BONUS = 0.15
_COMPETITION_PATTERNS = (
    re.compile(r"(?:отклик(?:ов|а)?|предложени(?:й|я)|bids?|respond(?:s|ed)?)\D{0,20}(\d{1,4})", re.IGNORECASE),
    re.compile(r"(?:offersCount|responsesCount|bidsCount)\D{0,10}(\d{1,4})", re.IGNORECASE),
)


class ExplanationActor(Protocol):
    """Actor capable of generating one explanation per candidate in a single call."""

    def explain_batch(self, user: User, candidates: list[Job]) -> list[str]:
        """Return why-it-fits strings aligned with *candidates* order."""
        ...


@dataclass(frozen=True)
class ACBatch:
    """Batch of pending jobs for one user."""

    user_id: int
    job_ids: list[int]


class ProcessACBatchUseCase:
    """Loads user/jobs, scores candidates locally, and emits batch notification."""

    def __init__(
        self,
        user_repo: object,
        job_repo: object,
        pending_repo: PendingJobsRepository,
        actor_factory: Callable[[User], ExplanationActor],
        notify_queue: object,
        *,
        subscription_guard: SubscriptionGuard | None = None,        feedback_repo: object | None = None,
        rerank_threshold: float = 0.55,
        rerank_fallback_enabled: bool = True,
        max_jobs_to_send: int = 5,
    ) -> None:
        self._user_repo = user_repo
        self._job_repo = job_repo
        self._pending_repo = pending_repo
        self._actor_factory = actor_factory
        self._notify_queue = notify_queue
        self._feedback_repo = feedback_repo
        self._subscription_guard = subscription_guard
        self._rerank_threshold = rerank_threshold
        self._rerank_fallback_enabled = rerank_fallback_enabled
        self._max_jobs_to_send = max_jobs_to_send

    def execute(self, batch: ACBatch) -> None:
        user = self._load_user(batch.user_id)
        if user is None or not user.profile_text:
            logger.warning("skip ac batch: no profile for user_id=%d", batch.user_id)
            self._pending_repo.mark_processed(batch.user_id, batch.job_ids)
            return
        if not user.embedding or len(user.embedding) == 0:
            logger.warning("skip ac batch: no embedding for user_id=%d", batch.user_id)
            self._release_batch(batch)
            return

        jobs_with_scores = self._load_jobs_with_scores(batch.user_id, batch.job_ids, user.embedding)
        if not jobs_with_scores:
            self._pending_repo.mark_processed(batch.user_id, batch.job_ids)
            return

        ranked_jobs = self._rank_jobs(user, jobs_with_scores)
        logger.info(
            "ac_batch user_id=%d jobs=%d selected=%d rerank_threshold=%.2f",
            batch.user_id,
            len(jobs_with_scores),
            len(ranked_jobs),
            self._rerank_threshold,
        )

        if ranked_jobs:
            self._enqueue_batch_notification(
                user_id=batch.user_id,
                ranked_jobs=ranked_jobs,
                batch_score=self._batch_score(ranked_jobs),
            )

        self._pending_repo.mark_processed(batch.user_id, batch.job_ids)

    def _rank_jobs(
        self,
        user: User,
        jobs_with_scores: list[tuple[Job, float]],
    ) -> list[RankedJob]:
        feedback_cache: dict[tuple[str, ...], float] = {}
        eligible: list[Job] = []
        decision = apply_rerank_policy(
            [job for job, _ in jobs_with_scores],
            score_getter=lambda job: float(job.rerank_score or 0.0),
            threshold=self._rerank_threshold,
            top_k=len(jobs_with_scores),
            fallback_enabled=self._rerank_fallback_enabled,
            pipeline="ac_batch",
            entity_name="user",
            entity_id=user.id,
        )
        allowed_job_ids = {job.id for job in decision.selected}

        for job, _embedding_similarity in jobs_with_scores:
            rerank_score = float(job.rerank_score or 0.0)
            if job.id not in allowed_job_ids:
                logger.info(
                    "drop job_id=%d user_id=%d rerank_score=%.3f below threshold=%.2f",
                    job.id,
                    user.id,
                    rerank_score,
                    self._rerank_threshold,
                )
                continue
            if decision.used_fallback and "rerank_all_filtered_fallback" not in (job.reason_codes or []):
                job.reason_codes = list(job.reason_codes or []) + ["rerank_all_filtered_fallback"]
            feedback_net = self._feedback_signal(user.id, job, feedback_cache)
            preference_multiplier = self._preference_multiplier(user, job.source)
            time_decay_multiplier = self._time_decay_multiplier(job)
            competition_multiplier = self._competition_multiplier(job)
            tag_affinity_bonus = self._tag_affinity_bonus(user, job)

            # Unified feedback adjustment (shared with job pipeline)
            adjusted_rerank = adjust_score(rerank_score, feedback_net)
            feedback_bonus_for_log = adjusted_rerank - rerank_score  # delta for logging
            job.feedback_bonus = feedback_bonus_for_log  # persist for scoring components

            job.final_score = self._final_score(
                adjusted_rerank_score=adjusted_rerank,
                preference_multiplier=preference_multiplier,
                time_decay_multiplier=time_decay_multiplier,
                competition_multiplier=competition_multiplier,
                tag_affinity_bonus=tag_affinity_bonus,
            )
            logger.info(
                "ac_score user_id=%d job_id=%d rerank=%.3f "
                "feedback_net=%.3f feedback_delta=%.3f pref_mult=%.3f "
                "time_decay=%.3f competition=%.3f tag_affinity=%.3f final=%.3f",
                user.id,
                job.id,
                rerank_score,
                feedback_net,
                feedback_bonus_for_log,
                preference_multiplier,
                time_decay_multiplier,
                competition_multiplier,
                tag_affinity_bonus,
                job.final_score,
            )
            eligible.append(job)

        if not eligible:
            return []

        self._save_scoring_components(user.id, eligible)
        ordered_jobs = sorted(
            eligible,
            key=lambda item: (item.final_score, item.rerank_score, item.id),
            reverse=True,
        )[: self._max_jobs_to_send]

        # Dynamically create actor based on user's plan
        actor_instance = self._actor_factory(user)
        explanations = self._generate_explanations(user, ordered_jobs, actor_instance)
        ranked_jobs: list[RankedJob] = []
        for index, (job, why_it_fits) in enumerate(zip(ordered_jobs, explanations, strict=True), start=1):
            ranked_jobs.append(
                RankedJob(
                    job_id=job.id,
                    title=job.title,
                    why_it_fits=why_it_fits,
                    rank=index,
                    actor_confidence=max(0.0, min(1.0, float(job.final_score))),
                    final_score=float(job.final_score),
                    ranker_version=job.ranker_version,
                    reason_codes=tuple(job.reason_codes or ()),
                )
            )
        return ranked_jobs

    def _feedback_signal(
        self,
        user_id: int,
        job: Job,
        cache: dict[tuple[str, ...], float],
    ) -> float:
        if self._feedback_repo is None:
            return 0.0
        skills = tuple(sorted({str(skill).strip() for skill in (job.technologies or []) if str(skill).strip()}))
        if skills in cache:
            return cache[skills]
        get_feedback_signal = getattr(self._feedback_repo, "get_feedback_signal", None)
        if not callable(get_feedback_signal):
            return 0.0
        signal = get_feedback_signal(user_id, list(skills))
        value = float(getattr(signal, "net", 0.0))
        cache[skills] = value
        return value

    def _save_scoring_components(self, user_id: int, jobs: list[Job]) -> None:
        save_scoring_components = getattr(self._pending_repo, "save_scoring_components", None)
        if not callable(save_scoring_components):
            return
        save_scoring_components(
            user_id,
            [
                (
                    int(job.id),
                    float(job.rerank_score or 0.0),
                    float(job.feedback_bonus or 0.0),
                    float(job.preference_multiplier if job.preference_multiplier is not None else 1.0),
                    float(job.final_score or 0.0),
                )
                for job in jobs
            ],
        )

    def _generate_explanations(self, user: User, jobs: list[Job], actor_instance: ExplanationActor) -> list[str]:
        explain_batch = getattr(actor_instance, "explain_batch", None)
        if callable(explain_batch):
            explanations = explain_batch(user, jobs)
            if len(explanations) == len(jobs):
                return [str(item or "").strip()[:500] for item in explanations]
            logger.warning(
                "actor returned %d explanations for %d jobs; using fallback copy",
                len(explanations),
                len(jobs),
            )
        return [self._fallback_explanation(job) for job in jobs]

    @staticmethod
    def _fallback_explanation(job: Job) -> str:
        if job.technologies:
            return f"Подходит по стеку: {', '.join(job.technologies[:3])}."
        return "Подходит по итоговому скору рекомендаций."

    @staticmethod
    def _tag_affinity_bonus(user: User, job: Job) -> float:
        """Calculate tag affinity bonus based on user's historical feedback.

        Each matching tag adds TAG_AFFINITY_WEIGHT_PER_TAG (5%), capped at
        TAG_AFFINITY_MAX_BONUS (15%).
        """
        if not user.tag_affinity or not job.technologies:
            return 0.0
        bonus = 0.0
        for tech in job.technologies:
            tag = str(tech).strip().lower()
            if tag in user.tag_affinity:
                affinity = user.tag_affinity[tag]
                bonus += affinity * TAG_AFFINITY_WEIGHT_PER_TAG
        return min(bonus, TAG_AFFINITY_MAX_BONUS)

    @staticmethod
    def _preference_multiplier(user: User, source: str) -> float:
        preferred_sources = tuple(
            str(item).strip().lower()
            for item in user.preferences.preferred_sources
            if str(item).strip()
        )
        if not preferred_sources:
            return 1.0
        normalized_source = str(source or "").strip().lower()
        if normalized_source in set(preferred_sources):
            return PREFERRED_SOURCE_MULTIPLIER
        return NON_PREFERRED_SOURCE_MULTIPLIER

    @staticmethod
    def _final_score(
        *,
        adjusted_rerank_score: float,
        preference_multiplier: float,
        time_decay_multiplier: float,
        competition_multiplier: float,
        tag_affinity_bonus: float = 0.0,
    ) -> float:
        """Compute final score with unified feedback adjustment and tag affinity."""
        base = float(adjusted_rerank_score) * (1.0 + float(tag_affinity_bonus))
        return (
            base
            * float(preference_multiplier)
            * float(time_decay_multiplier)
            * float(competition_multiplier)
        )

    @staticmethod
    def _time_decay_multiplier(job: Job) -> float:
        posted_at = job.posted_at or job.created_at
        if posted_at is None:
            return 1.0
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        hours_since_posted = max(0.0, (now - posted_at).total_seconds() / 3600.0)
        return math.exp(-hours_since_posted / TIME_DECAY_HOURS)

    @staticmethod
    def _competition_multiplier(job: Job) -> float:
        if str(job.source or "").strip().lower() != "kwork":
            return 1.0
        bids = ProcessACBatchUseCase._extract_competition_count(job.raw_html or "")
        if bids is None:
            return 1.0
        return 1.0 / (1.0 + (float(max(0, bids)) / COMPETITION_SCALE))

    @staticmethod
    def _extract_competition_count(raw_html: str) -> int | None:
        if not raw_html:
            return None
        values: list[int] = []
        for pattern in _COMPETITION_PATTERNS:
            for raw in pattern.findall(raw_html):
                try:
                    value = int(str(raw))
                except ValueError:
                    continue
                if 0 <= value <= 5000:
                    values.append(value)
        if not values:
            return None
        return max(values)

    @staticmethod
    def _batch_score(ranked_jobs: list[RankedJob]) -> float:
        if not ranked_jobs:
            return 0.0
        return sum(item.final_score for item in ranked_jobs) / len(ranked_jobs) * 10.0

    def _release_batch(self, batch: ACBatch) -> None:
        release_claim = getattr(self._pending_repo, "release_claim", None)
        if callable(release_claim):
            release_claim(batch.user_id, batch.job_ids)
            return
        logger.warning("pending repo has no release_claim; batch stays leased user_id=%d", batch.user_id)

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
        batch_score: float,
    ) -> None:
        enqueue_batch = getattr(self._notify_queue, "enqueue_batch", None)
        if callable(enqueue_batch):
            enqueue_batch(
                user_id=user_id,
                ranked_jobs=ranked_jobs,
                batch_score=batch_score,
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
