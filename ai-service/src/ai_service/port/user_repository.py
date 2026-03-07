"""Port: UserRepository — абстракция доступа к users."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_service.domain.user import User


class UserRepository(ABC):
    """Репозиторий для работы с пользователями."""

    @abstractmethod
    def save(self, telegram_id: int) -> int:
        """Создать пользователя. Возвращает user_id."""
        ...

    @abstractmethod
    def get_by_id(self, user_id: int) -> User | None:
        """Получить пользователя по id."""
        ...

    @abstractmethod
    def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """Получить пользователя по telegram_id."""
        ...

    @abstractmethod
    def get_embedding(self, user_id: int) -> list[float] | None:
        """Получить embedding пользователя по id."""
        ...

    @abstractmethod
    def update_profile(self, user_id: int, profile_text: str) -> None:
        """Обновить profile_text пользователя."""
        ...

    @abstractmethod
    def save_embedding(self, user_id: int, embedding: list[float]) -> None:
        """Сохранить embedding пользователя."""
        ...
