"""Domain entity: User."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UserPreferences:
    """Explicit user preferences used by ranker personalization."""

    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    min_budget: float | None = None
    max_budget: float | None = None
    preferred_sources: tuple[str, ...] = ()


@dataclass
class User:
    """User from users table."""

    id: int
    telegram_id: int
    profile_text: str | None
    plan_id: str = "free" # default to free plan
    embedding: list[float] | None
    preferences: UserPreferences = field(default_factory=UserPreferences)
    tag_affinity: dict[str, float] = field(default_factory=dict)
