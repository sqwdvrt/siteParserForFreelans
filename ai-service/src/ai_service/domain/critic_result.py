"""Domain model: critic evaluation output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CriticResult:
    """Critic score with textual feedback for next Actor iteration."""

    score: float
    critique: str
