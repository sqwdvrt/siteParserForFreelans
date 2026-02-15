"""Port: JobRepository — абстракция доступа к jobs и job_embeddings."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_service.domain.job import Job


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
