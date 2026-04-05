"""ProcessJobUseCase: get → clean → classify → encode → save → matching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from ai_service.port.classifier import ClassificationResult
from ai_service.port.embedding import EmbeddingService
from ai_service.port.match_repository import MatchRepository
from ai_service.port.repository import JobRepository
from ai_service.usecase.rerank_policy import apply_rerank_policy
from ai_service.util.feedback_adjuster import adjust_candidates
from ai_service.util.preference_filter import (
    PREFERENCE_FILTER_REASON_BUDGET,
    evaluate_preference_filter,
)
from ai_service.util.profile_structurer import build_structured_profile_text
from ai_service.util.text_cleaner import clean_text
from ai_service.util.trace_context import get_trace_id

if TYPE_CHECKING:
    from ai_service.port.classifier import Classifier
    from ai_service.port.filter_event_repository import FilterEventRepository
    from ai_service.port.match_repository import MatchCandidate
    from ai_service.port.user_repository import UserRepository
    from ai_service.usecase.accumulate_matches import AccumulateMatchesUseCase

    class Reranker(Protocol):
        """Minimal protocol for query/document reranking."""

        @property
        def model_name(self) -> str:
            """Human-readable model name for logging/metadata."""
            ...

        def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
            """Return normalized rerank scores in 0..1."""
            ...

logger = logging.getLogger(__name__)
DEFAULT_ANN_TOP_K = 50

_PROJECT_TYPE_LABELS: dict[str, str] = {
    "web": "Веб-проект",
    "mobile": "Мобильное приложение",
    "bot": "Telegram-бот",
    "other": "Проект",
}
_SENIORITY_LABELS: dict[str, str] = {
    "junior": "junior",
    "middle": "middle",
    "senior": "senior",
}
_BUDGET_LABELS: dict[str, str] = {
    "low": "небольшой бюджет",
    "medium": "средний бюджет",
    "high": "высокий бюджет",
}


def _build_why_it_fits(classification: ClassificationResult) -> str:
    """Сформировать 1–2 фразы о проекте из данных классификатора."""
    if not classification:
        return ""

    project_type = classification.get("project_type", "other")
    label = _PROJECT_TYPE_LABELS.get(str(project_type), "Проект")

    techs = classification.get("technologies") or []
    if techs:
        first_part = f"{label} со стеком {', '.join(techs[:3])}"
    else:
        first_part = label

    sub_parts: list[str] = []
    seniority = classification.get("seniority", "unknown")
    if seniority and seniority != "unknown":
        sub_parts.append(f"уровень: {_SENIORITY_LABELS.get(str(seniority), str(seniority))}")
    budget = classification.get("budget_level", "unknown")
    if budget and budget != "unknown":
        budget_label = _BUDGET_LABELS.get(str(budget), "")
        if budget_label:
            sub_parts.append(budget_label)

    parts = [first_part]
    if sub_parts:
        parts.append(", ".join(sub_parts))
    return ". ".join(parts) + "."


class ProcessJobUseCase:
    """Обработка job: получить, очистить, классифицировать, закодировать, сохранить embedding, matching."""

    def __init__(
        self,
        repo: JobRepository,
        embedding_service: EmbeddingService,
        classifier: Classifier | None = None,
        match_repo: MatchRepository | None = None,
        accumulate_matches: AccumulateMatchesUseCase | None = None,
        reranker: Reranker | None = None,
        *,
        similarity_threshold: float = 0.7,
        max_matches_per_job: int = 20,
        match_max_age_days: int | None = None,
        rerank_threshold: float = 0.55,
        rerank_top_k: int = 10,
        rerank_fallback_enabled: bool = True,
        feedback_repo=None,
        user_repo: UserRepository | None = None,
        filter_event_repo: FilterEventRepository | None = None,
    ) -> None:
        self._repo = repo
        self._embedding = embedding_service
        self._classifier = classifier
        self._match_repo = match_repo
        self._accumulate_matches = accumulate_matches
        self._reranker = reranker
        self._threshold = similarity_threshold
        self._limit = max_matches_per_job
        self._match_max_age_days = match_max_age_days
        self._rerank_threshold = rerank_threshold
        self._rerank_top_k = rerank_top_k
        self._rerank_fallback_enabled = rerank_fallback_enabled
        self._feedback_repo = feedback_repo
        self._user_repo = user_repo
        self._filter_event_repo = filter_event_repo

    def execute(self, job_id: int) -> bool:
        """Process job using a new or previously saved embedding.

        Returns True when matching was attempted, False only when the job is
        missing or saved embedding cannot be loaded for retry.
        """
        trace_id = get_trace_id()
        job = self._repo.get(job_id)
        if job is None:
            if trace_id:
                logger.warning("job not found: %s trace_id=%s", job_id, trace_id)
            else:
                logger.warning("job not found: %s", job_id)
            return False

        classification: ClassificationResult = {}
        embedding: list[float] = []
        saved_embedding = self._repo.get_embedding(job_id)
        if saved_embedding is not None:
            embedding = saved_embedding.embedding
            raw_classification = saved_embedding.metadata.get("classification")
            if isinstance(raw_classification, dict):
                classification = raw_classification
            if trace_id:
                logger.info("embedding already exists, continue matching job_id=%s trace_id=%s", job_id, trace_id)
            else:
                logger.info("embedding already exists, continue matching job_id=%s", job_id)
        else:
            raw = f"{job.title} {job.description or ''} {job.raw_html}"
            text = clean_text(raw)
            embedding = self._embedding.encode(text)

            metadata: dict = {
                "model": self._embedding.model_name,
                "text_length": len(text),
            }
            if self._classifier is not None:
                classification = self._classifier.classify(text)
                if classification:
                    metadata["classification"] = classification

            self._repo.save_embedding(job_id, embedding, metadata)
            if trace_id:
                logger.info("saved embedding for job_id=%s trace_id=%s", job_id, trace_id)
            else:
                logger.info("saved embedding for job_id=%s", job_id)

        if embedding is None or len(embedding) == 0:
            if trace_id:
                logger.warning("saved embedding missing for job_id=%s trace_id=%s", job_id, trace_id)
            else:
                logger.warning("saved embedding missing for job_id=%s", job_id)
            return False

        if self._match_repo is not None:
            allowed_user_ids: list[int] | None = None
            if self._user_repo is not None:
                matchable_users = self._user_repo.list_matchable_users()
                filter_result = evaluate_preference_filter(job, matchable_users, classification=classification)
                filtered_users = filter_result.passed
                allowed_user_ids = [user.id for user in filtered_users]
                filtered_count = len(matchable_users) - len(filtered_users)
                self._record_budget_filtered_users(
                    job_id=job_id,
                    filtered_user_reasons=filter_result.filtered_user_reasons,
                    trace_id=trace_id or "",
                )
                if filtered_count > 0:
                    if trace_id:
                        logger.info(
                            "job_id=%s trace_id=%s: preference pre-filter excluded %d/%d users "
                            "reason_code=pref_filtered",
                            job_id,
                            trace_id,
                            filtered_count,
                            len(matchable_users),
                        )
                    else:
                        logger.info(
                            "job_id=%s: preference pre-filter excluded %d/%d users reason_code=pref_filtered",
                            job_id,
                            filtered_count,
                            len(matchable_users),
                        )
                if not allowed_user_ids:
                    if trace_id:
                        logger.info("job_id=%s trace_id=%s: no users passed preference pre-filter", job_id, trace_id)
                    else:
                        logger.info("job_id=%s: no users passed preference pre-filter", job_id)
                    return True
            candidates = self._match_repo.find_users_for_job(
                embedding,
                job_id,
                self._threshold,
                max(self._limit, DEFAULT_ANN_TOP_K) if self._reranker is not None else self._limit,
                allowed_user_ids=allowed_user_ids,
                max_age_days=self._match_max_age_days,
            )
            if trace_id:
                logger.info("job_id=%s trace_id=%s: %d match candidates", job_id, trace_id, len(candidates))
            else:
                logger.info("job_id=%s: %d match candidates", job_id, len(candidates))

            if self._reranker is not None and candidates:
                candidates = self._rerank_candidates(job, candidates)

            # Корректировка скоров на основе обратной связи (👍/👎).
            # Учитывает как позитивный (буст до +15%), так и негативный (штраф до -50%) сигнал.
            # Сигнал считается по навыкам job + глобально, результаты смешиваются 50/50.
            if self._feedback_repo is not None and candidates:
                job_skills = getattr(job, "skills", None) or []
                candidates = adjust_candidates(
                    candidates,
                    get_job_skills=lambda _: job_skills,
                    feedback_repo=self._feedback_repo,
                    threshold=self._threshold,
                )

            if candidates:
                why_it_fits = _build_why_it_fits(classification)
                for c in candidates:
                    c.why_it_fits = why_it_fits
                    if trace_id:
                        c.trace_id = trace_id
                if self._accumulate_matches is not None:
                    self._accumulate_matches.execute(candidates)

        return True

    def _record_budget_filtered_users(
        self,
        *,
        job_id: int,
        filtered_user_reasons: dict[int, set[str]],
        trace_id: str,
    ) -> None:
        if self._filter_event_repo is None or not filtered_user_reasons:
            return
        budget_filtered_user_ids = [
            int(user_id)
            for user_id, reasons in filtered_user_reasons.items()
            if PREFERENCE_FILTER_REASON_BUDGET in reasons
        ]
        if not budget_filtered_user_ids:
            return
        try:
            self._filter_event_repo.record_events(
                job_id=job_id,
                user_ids=budget_filtered_user_ids,
                reason=PREFERENCE_FILTER_REASON_BUDGET,
                trace_id=trace_id,
            )
        except Exception as e:
            logger.warning(
                "job_id=%s: failed to persist budget filter events count=%d err=%s",
                job_id,
                len(budget_filtered_user_ids),
                e,
            )

    def _rerank_candidates(self, job: object, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        """Rerank ANN candidates with cross-encoder before persisting pending rows."""
        if self._reranker is None or not candidates:
            return candidates

        job_title = getattr(job, "title", "") or ""
        job_description = getattr(job, "description", "") or ""
        job_text = clean_text(f"{job_title}\n{job_description}".strip())

        profiles = [
            build_structured_profile_text(str(candidate.profile_text or "").strip())
            for candidate in candidates
        ]
        encoder_indices = [i for i, p in enumerate(profiles) if p]

        if not encoder_indices:
            logger.warning(
                "job_id=%s: all %d rerank candidates have empty profile_text, "
                "skipping cross-encoder",
                getattr(job, "id", 0),
                len(candidates),
            )
            return candidates

        empty_count = len(candidates) - len(encoder_indices)
        if empty_count:
            logger.debug(
                "job_id=%s: %d/%d candidates have empty profile_text, "
                "using raw_similarity as rerank_score for them",
                getattr(job, "id", 0),
                empty_count,
                len(candidates),
            )
            for i, candidate in enumerate(candidates):
                if not profiles[i]:
                    candidate.rerank_score = candidate.raw_similarity
                    candidate.final_score = candidate.raw_similarity

        pairs = [(profiles[i], job_text) for i in encoder_indices]
        rerank_scores = self._reranker.score_pairs(pairs)
        if len(rerank_scores) != len(encoder_indices):
            raise ValueError(
                f"reranker returned {len(rerank_scores)} scores for {len(encoder_indices)} candidates"
            )

        for idx, rerank_score in zip(encoder_indices, rerank_scores):
            candidates[idx].rerank_score = rerank_score
            # Keep final_score aligned with rerank output until downstream scoring takes over.
            candidates[idx].final_score = rerank_score

        ranked = sorted(
            candidates,
            key=lambda candidate: (candidate.rerank_score, candidate.raw_similarity, candidate.user_id),
            reverse=True,
        )
        decision = apply_rerank_policy(
            ranked,
            score_getter=lambda candidate: candidate.rerank_score,
            threshold=self._rerank_threshold,
            top_k=self._rerank_top_k,
            fallback_enabled=self._rerank_fallback_enabled,
            pipeline="job",
            entity_name="job",
            entity_id=getattr(job, "id", 0),
        )
        filtered = decision.selected
        if decision.used_fallback:
            for candidate in filtered:
                if "rerank_all_filtered_fallback" not in candidate.reason_codes:
                    candidate.reason_codes = tuple(candidate.reason_codes) + ("rerank_all_filtered_fallback",)

        trace_id = get_trace_id()
        best_score = decision.best_score_before_filter
        if trace_id:
            logger.info(
                "job_id=%s trace_id=%s: reranked %d/%d candidates "
                "model=%s threshold=%.2f top_k=%d best_before_filter=%.3f "
                "fallback=%s",
                getattr(job, "id", 0),
                trace_id,
                len(filtered),
                len(candidates),
                self._reranker.model_name,
                self._rerank_threshold,
                self._rerank_top_k,
                best_score,
                decision.used_fallback,
            )
        else:
            logger.info(
                "job_id=%s: reranked %d/%d candidates model=%s "
                "threshold=%.2f top_k=%d best_before_filter=%.3f fallback=%s",
                getattr(job, "id", 0),
                len(filtered),
                len(candidates),
                self._reranker.model_name,
                self._rerank_threshold,
                self._rerank_top_k,
                best_score,
                decision.used_fallback,
            )
        return filtered
