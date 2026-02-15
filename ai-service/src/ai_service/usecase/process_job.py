"""ProcessJobUseCase: get → clean → classify → encode → save."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ai_service.port.embedding import EmbeddingService
from ai_service.port.repository import JobRepository
from ai_service.util.text_cleaner import clean_text

if TYPE_CHECKING:
    from ai_service.port.classifier import Classifier

logger = logging.getLogger(__name__)


class ProcessJobUseCase:
    """Обработка job: получить, очистить, классифицировать, закодировать, сохранить embedding."""

    def __init__(
        self,
        repo: JobRepository,
        embedding_service: EmbeddingService,
        classifier: Classifier | None = None,
    ) -> None:
        self._repo = repo
        self._embedding = embedding_service
        self._classifier = classifier

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
        if self._classifier is not None:
            classification = self._classifier.classify(text)
            if classification:
                metadata["classification"] = classification

        self._repo.save_embedding(job_id, embedding, metadata)
        logger.info("saved embedding for job_id=%s", job_id)
        return True
