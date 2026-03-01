"""Domain model: ranked job selected by Actor."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankedJob:
    """Job selected for user with Actor ranking/explanation."""

    job_id: int
    title: str
    why_it_fits: str
    rank: int
    actor_confidence: float
