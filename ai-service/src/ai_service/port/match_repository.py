"""Port: MatchRepository — поиск пользователей по similarity embedding."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MatchCandidate:
    """Кандидат на уведомление: user_id, job_id, match_score (0–1), why_it_fits (опционально)."""

    user_id: int
    job_id: int
    match_score: float
    why_it_fits: str = ""


class MatchRepository(ABC):
    """Репозиторий для поиска пользователей по similarity к embedding."""

    @abstractmethod
    def find_users_for_job(
        self,
        embedding: list[float],
        job_id: int,
        threshold: float,
        limit: int = 100,
    ) -> list[MatchCandidate]:
        """
        Найти пользователей для job по similarity.
        score = 1 - (embedding <=> query), cosine similarity.
        Исключает пользователей, уже в notifications для этого job_id.
        match_score = similarity (0–1), эвристики можно добавить.
        """
        ...
