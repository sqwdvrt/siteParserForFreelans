"""Domain entities for AI Service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Job:
    """Job from jobs table (for embedding)."""

    id: int
    title: str
    description: str | None
    raw_html: str
    budget: str = ""
    source: str = "kwork"
    url: str = ""
    technologies: list[str] | None = None
    posted_at: datetime | None = None
    created_at: datetime | None = None
    match_score: float = 0.0
    rerank_score: float = 0.0
    raw_similarity: float = 0.0
    feedback_bonus: float = 0.0
    preference_multiplier: float = 1.0
    final_score: float = 0.0
    ranker_version: str = ""
    reason_codes: list[str] | None = None


@dataclass
class JobEmbedding:
    """Job embedding result."""

    job_id: int
    embedding: list[float]
    metadata: dict
