"""Port: UserEmbedQueueConsumer — абстракция получения user_id из очереди user-embed."""

from __future__ import annotations

from abc import ABC, abstractmethod


class UserEmbedQueueConsumer(ABC):
    """Consumer очереди user-embed. BRPOP, парсинг JSON {"user_id": N}."""

    @abstractmethod
    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        """Забрать одно сообщение из очереди. None при таймауте или пустой очереди."""
        ...
