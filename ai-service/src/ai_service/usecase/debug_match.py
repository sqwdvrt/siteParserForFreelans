"""Debug matching diagnostics for a specific user and project."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ai_service.util.preference_filter import evaluate_preference_filter
from ai_service.util.profile_structurer import build_structured_profile_text, extract_structured_profile
from ai_service.util.text_cleaner import clean_text

if TYPE_CHECKING:
    from ai_service.domain.job import Job
    from ai_service.domain.user import User
    from ai_service.port.repository import JobRepository
    from ai_service.port.user_repository import UserRepository


class DebugMatchUseCase:
    """Calculate debug matching metrics for one user and one job URL."""

    def __init__(
        self,
        user_repo: "UserRepository",
        job_repo: "JobRepository",
        *,
        reranker=None,
        similarity_threshold: float = 0.62,
        rerank_threshold: float = 0.60,
    ) -> None:
        self._user_repo = user_repo
        self._job_repo = job_repo
        self._reranker = reranker
        self._similarity_threshold = similarity_threshold
        self._rerank_threshold = rerank_threshold

    def execute(self, user_id: int, job_url: str) -> dict[str, object]:
        user = self._user_repo.get_by_id(user_id)
        if user is None:
            return {"error": "user_not_found", "user_id": user_id, "job_url": job_url}

        job = self._job_repo.get_by_url(job_url)
        if job is None:
            return {"error": "job_not_found", "user_id": user_id, "job_url": job_url}

        user_embedding = self._user_repo.get_embedding(user_id)
        job_embedding_record = self._job_repo.get_embedding(job.id)
        job_embedding = None
        classification: dict[str, object] = {}
        if job_embedding_record is not None:
            job_embedding = job_embedding_record.embedding
            raw_classification = job_embedding_record.metadata.get("classification")
            if isinstance(raw_classification, dict):
                classification = raw_classification

        similarity = _cosine_similarity(user_embedding, job_embedding)
        rerank_score = self._compute_rerank_score(user, job)
        filter_result = evaluate_preference_filter(job, [user], classification=classification)
        preference_passed = bool(filter_result.passed)
        filter_reasons = sorted(filter_result.filtered_user_reasons.get(user.id, set()))
        profile_stack = _profile_stack_for_user(user)
        job_stack = _job_stack_for_debug(job, classification)
        stack_intersection = sorted(set(profile_stack) & set(job_stack))

        return {
            "user_id": user_id,
            "job_url": job_url,
            "project": {
                "id": job.id,
                "title": job.title,
                "source": job.source,
                "url": job.url,
            },
            "embedding_similarity": similarity,
            "similarity_threshold": self._similarity_threshold,
            "rerank_score": rerank_score,
            "rerank_threshold": self._rerank_threshold,
            "preference_filter": {
                "passed": preference_passed,
                "reason": filter_reasons[0] if filter_reasons else "",
                "reasons": filter_reasons,
            },
            "final_score": _compute_debug_final_score(
                rerank_score=rerank_score,
                source=job.source,
                posted_at=job.posted_at or job.created_at,
                raw_html=job.raw_html or "",
                preferred_sources=tuple(user.preferences.preferred_sources),
            ),
            "job_stack": job_stack,
            "profile_stack": profile_stack,
            "stack_intersection": stack_intersection,
            "conclusion": _build_conclusion(
                similarity=similarity,
                similarity_threshold=self._similarity_threshold,
                rerank_score=rerank_score,
                rerank_threshold=self._rerank_threshold,
                preference_passed=preference_passed,
                filter_reasons=filter_reasons,
            ),
        }

    def _compute_rerank_score(self, user: "User", job: "Job") -> float | None:
        if self._reranker is None:
            return None
        profile_text = build_structured_profile_text(str(user.profile_text or "").strip())
        job_text = clean_text(f"{job.title}\n{job.description or ''}".strip())
        if not profile_text or not job_text:
            return None
        scores = self._reranker.score_pairs([(profile_text, job_text)])
        if not scores:
            return None
        return float(scores[0])


def _cosine_similarity(lhs: list[float] | None, rhs: list[float] | None) -> float | None:
    if not lhs or not rhs or len(lhs) != len(rhs):
        return None
    dot = sum(float(a) * float(b) for a, b in zip(lhs, rhs, strict=True))
    lhs_norm = math.sqrt(sum(float(value) * float(value) for value in lhs))
    rhs_norm = math.sqrt(sum(float(value) * float(value) for value in rhs))
    if lhs_norm == 0 or rhs_norm == 0:
        return None
    return dot / (lhs_norm * rhs_norm)


def _profile_stack_for_user(user: "User") -> list[str]:
    explicit = [str(item).strip().lower() for item in user.preferences.include_keywords if str(item).strip()]
    if explicit:
        return explicit
    return list(extract_structured_profile(user.profile_text or "").stack)


def _job_stack_for_debug(job: "Job", classification: dict[str, object]) -> list[str]:
    stack: list[str] = []
    technologies = classification.get("technologies")
    if isinstance(technologies, list):
        for item in technologies:
            token = _normalize_stack_token(item)
            if token and token not in stack:
                stack.append(token)
    for source in (job.title, job.description or ""):
        haystack = str(source or "").lower()
        for token in ("python", "django", "fastapi", "postgresql", "postgres", "aiogram", "redis", "docker"):
            if token in haystack:
                normalized = _normalize_stack_token(token)
                if normalized and normalized not in stack:
                    stack.append(normalized)
    return stack


def _normalize_stack_token(value: object) -> str:
    token = str(value or "").strip().lower()
    if token == "postgres":
        return "postgresql"
    return token


def _compute_debug_final_score(
    *,
    rerank_score: float | None,
    source: str,
    posted_at: datetime | None,
    raw_html: str,
    preferred_sources: tuple[str, ...],
) -> float | None:
    if rerank_score is None:
        return None
    normalized_source = str(source or "").strip().lower()
    preferred = {str(item).strip().lower() for item in preferred_sources if str(item).strip()}
    preference_multiplier = 1.0
    if preferred:
        preference_multiplier = 1.2 if normalized_source in preferred else 0.8

    time_decay_multiplier = 1.0
    if posted_at is not None:
        timestamp = posted_at if posted_at.tzinfo is not None else posted_at.replace(tzinfo=timezone.utc)
        hours_since = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 3600.0)
        time_decay_multiplier = math.exp(-hours_since / 48.0)

    competition_multiplier = 1.0
    if normalized_source == "kwork" and "offers-count" in raw_html:
        competition_multiplier = 0.9

    return float(rerank_score) * preference_multiplier * time_decay_multiplier * competition_multiplier


def _build_conclusion(
    *,
    similarity: float | None,
    similarity_threshold: float,
    rerank_score: float | None,
    rerank_threshold: float,
    preference_passed: bool,
    filter_reasons: list[str],
) -> str:
    if similarity is None:
        return "нет user/job embedding, ANN-диагностика недоступна"
    if similarity < similarity_threshold:
        return f"не прошёл бы ANN порог ({similarity:.2f} < {similarity_threshold:.2f})"
    if rerank_score is None:
        return "ANN прошёл бы, но rerank недоступен"
    if rerank_score < rerank_threshold:
        return f"не прошёл бы rerank порог ({rerank_score:.2f} < {rerank_threshold:.2f})"
    if not preference_passed:
        reason = filter_reasons[0] if filter_reasons else "unknown"
        return f"отсечён preference filter ({reason})"
    return "прошёл бы ANN, rerank и preference filter"
