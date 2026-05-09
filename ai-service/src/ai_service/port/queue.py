"""Port: JobQueueConsumer — абстракция получения job_id из очереди."""

from __future__ import annotations

from abc import ABC, abstractmethod


class JobQueueConsumer(ABC):
    """Consumer очереди ai-process. BRPOP, парсинг JSON."""

    @abstractmethod
    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        """Забрать одно сообщение из очереди. None при таймауте или пустой очереди."""
        ...
