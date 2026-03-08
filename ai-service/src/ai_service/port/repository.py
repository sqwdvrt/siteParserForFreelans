"""Port: JobRepository — абстракция доступа к jobs и job_embeddings."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ai_service.domain.job import Job


@dataclass(frozen=True)
class JobEmbeddingRecord:
    """Saved job embedding plus optional model metadata."""

    embedding: list[float]
    metadata: dict[str, object] = field(default_factory=dict)


class JobRepository(ABC):
    """Репозиторий для работы с job и embeddings."""

    @abstractmethod
    def get(self, job_id: int) -> Job | None:
        """Получить job по id. None если не найден."""
        ...

    @abstractmethod
    def save_embedding(
        self,
        job_id: int,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        """Сохранить embedding в job_embeddings."""
        ...

    @abstractmethod
    def has_embedding(self, job_id: int) -> bool:
        """Проверить наличие embedding для job_id."""
        ...

    @abstractmethod
    def get_embedding(self, job_id: int) -> JobEmbeddingRecord | None:
        """Load saved job embedding and metadata for retry-safe reprocessing."""
        ...

    @abstractmethod
    def has_recent_similar_title(self, job_id: int, title: str, days: int = 7) -> bool:
        """Проверить, есть ли недавняя задача с похожим нормализованным заголовком."""
        ...
