"""Domain entity: User."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    """User from users table."""

    id: int
    telegram_id: int
    profile_text: str | None
    embedding: list[float] | None
