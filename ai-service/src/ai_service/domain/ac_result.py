"""Domain model: final output of Actor-Critic loop."""

from __future__ import annotations

from dataclasses import dataclass

from ai_service.domain.ranked_job import RankedJob


@dataclass(frozen=True)
class ActorCriticResult:
    """Result of Actor-Critic selection loop."""

    selection: list[RankedJob]
    final_score: float
    attempts: int
    passed: bool
