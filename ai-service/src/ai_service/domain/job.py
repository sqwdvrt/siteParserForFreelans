"""Domain entities for AI Service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Job:
    """Job from jobs table (for embedding)."""

    id: int
    title: str
    description: str | None
    raw_html: str


@dataclass
class JobEmbedding:
    """Job embedding result."""

    job_id: int
    embedding: list[float]
    metadata: dict
