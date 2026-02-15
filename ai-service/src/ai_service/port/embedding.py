"""Port: EmbeddingService — абстракция кодирования текста в вектор."""

from abc import ABC, abstractmethod


class EmbeddingService(ABC):
    """Сервис кодирования текста в вектор фиксированной размерности (384)."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Имя модели для ai_metadata."""
        ...

    @abstractmethod
    def encode(self, text: str) -> list[float]:
        """Закодировать текст в вектор. Размерность 384."""
        ...
