"""ProcessJobUseCase: get → clean → classify → encode → save → matching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ai_service.port.classifier import ClassificationResult
from ai_service.port.embedding import EmbeddingService
from ai_service.port.match_repository import MatchRepository
from ai_service.port.match_notify_queue import MatchNotifyQueue
from ai_service.port.repository import JobRepository
from ai_service.util.text_cleaner import clean_text

if TYPE_CHECKING:
    from ai_service.port.classifier import Classifier

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
        match_notify_queue: MatchNotifyQueue | None = None,
        *,
        similarity_threshold: float = 0.7,
        max_matches_per_job: int = 20,
    ) -> None:
        self._repo = repo
        self._embedding = embedding_service
        self._classifier = classifier
        self._match_repo = match_repo
        self._match_notify_queue = match_notify_queue
        self._threshold = similarity_threshold
        self._limit = max_matches_per_job

    def execute(self, job_id: int) -> bool:
        """Обработать job_id. Возвращает True если embedding сохранён, False если пропущен."""
        job = self._repo.get(job_id)
        if job is None:
            logger.warning("job not found: %s", job_id)
            return False

        if self._repo.has_embedding(job_id):
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
        logger.info("saved embedding for job_id=%s", job_id)

        if self._match_repo is not None:
            candidates = self._match_repo.find_users_for_job(
                embedding, job_id, self._threshold, self._limit
            )
            logger.info("job_id=%s: %d match candidates", job_id, len(candidates))
            if self._match_notify_queue is not None and candidates:
                why_it_fits = _build_why_it_fits(classification)
                for c in candidates:
                    c.why_it_fits = why_it_fits
                enqueue_many = getattr(self._match_notify_queue, "enqueue_many", None)
                if callable(enqueue_many):
                    enqueue_many(candidates)
                else:
                    for c in candidates:
                        self._match_notify_queue.enqueue(c)

        return True
