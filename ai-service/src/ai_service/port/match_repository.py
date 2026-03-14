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
    rerank_score: float = 0.0
    raw_similarity: float = 0.0
    final_score: float = 0.0
    profile_text: str = ""
    why_it_fits: str = ""
    trace_id: str = ""
    ranker_version: str = ""
    reason_codes: tuple[str, ...] = ()


class MatchRepository(ABC):
    """Репозиторий для поиска пользователей по similarity к embedding."""

    @abstractmethod
    def find_users_for_job(
        self,
        embedding: list[float],
        job_id: int,
        threshold: float,
        limit: int = 100,
        allowed_user_ids: list[int] | None = None,
    ) -> list[MatchCandidate]:
        """
        Найти пользователей для job по similarity.
        score = 1 - (embedding <=> query), cosine similarity.
        Исключает пользователей, уже в notifications для этого job_id.
        match_score = similarity (0–1), эвристики можно добавить.
        """
        ...

    @abstractmethod
    def find_jobs_for_user(
        self,
        embedding: list[float],
        user_id: int,
        threshold: float,
        limit: int = 100,
        days_back: int = 7,
    ) -> list[MatchCandidate]:
        """Найти recent jobs для пользователя по similarity."""
        ...
