"""Port: UserRematchQueue - enqueue user_id for rematch against existing jobs."""

from __future__ import annotations

from abc import ABC, abstractmethod


class UserRematchQueue(ABC):
    """Queue for re-matching one user against recent jobs."""

    @abstractmethod
    def enqueue(self, user_id: int) -> None:
        """Enqueue user_id for rematch processing."""
        ...
