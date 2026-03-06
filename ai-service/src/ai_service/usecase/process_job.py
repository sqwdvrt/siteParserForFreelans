"""ProcessJobUseCase: get → clean → classify → encode → save → matching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ai_service.port.classifier import ClassificationResult
from ai_service.port.embedding import EmbeddingService
from ai_service.port.match_repository import MatchRepository
from ai_service.port.repository import JobRepository
from ai_service.util.feedback_adjuster import adjust_candidates
from ai_service.util.text_cleaner import clean_text
from ai_service.util.trace_context import get_trace_id

if TYPE_CHECKING:
    from ai_service.port.classifier import Classifier
    from ai_service.usecase.accumulate_matches import AccumulateMatchesUseCase

logger = logging.getLogger(__name__)

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
        *,
        similarity_threshold: float = 0.7,
        max_matches_per_job: int = 20,
        feedback_repo=None,
    ) -> None:
        self._repo = repo
        self._embedding = embedding_service
        self._classifier = classifier
        self._match_repo = match_repo
        self._accumulate_matches = accumulate_matches
        self._threshold = similarity_threshold
        self._limit = max_matches_per_job
        self._feedback_repo = feedback_repo

    def execute(self, job_id: int) -> bool:
        """Обработать job_id. Возвращает True если embedding сохранён, False если пропущен."""
        trace_id = get_trace_id()
        job = self._repo.get(job_id)
        if job is None:
            if trace_id:
                logger.warning("job not found: %s trace_id=%s", job_id, trace_id)
            else:
                logger.warning("job not found: %s", job_id)
            return False

        if self._repo.has_embedding(job_id):
            if trace_id:
                logger.debug("embedding already exists, skip: %s trace_id=%s", job_id, trace_id)
            else:
                logger.debug("embedding already exists, skip: %s", job_id)
            return False

        raw = f"{job.title} {job.description or ''} {job.raw_html}"
        text = clean_text(raw)
        embedding = self._embedding.encode(text)

        metadata: dict = {
            "model": self._embedding.model_name,
            "text_length": len(text),
        }
        classification: ClassificationResult = {}
        if self._classifier is not None:
            classification = self._classifier.classify(text)
            if classification:
                metadata["classification"] = classification

        self._repo.save_embedding(job_id, embedding, metadata)
        if trace_id:
            logger.info("saved embedding for job_id=%s trace_id=%s", job_id, trace_id)
        else:
            logger.info("saved embedding for job_id=%s", job_id)

        if self._match_repo is not None:
            candidates = self._match_repo.find_users_for_job(
                embedding, job_id, self._threshold, self._limit
            )
            if trace_id:
                logger.info("job_id=%s trace_id=%s: %d match candidates", job_id, trace_id, len(candidates))
            else:
                logger.info("job_id=%s: %d match candidates", job_id, len(candidates))

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
