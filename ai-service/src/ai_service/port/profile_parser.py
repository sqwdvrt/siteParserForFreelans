"""Port: ProfileParser — structured extraction from user profile text."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypedDict


class ParsedProfile(TypedDict):
    """Structured freelancer profile extracted from free text."""

    stack: list[str]
    specialization: str
    level: str
    preferred_work_type: str
    min_budget_hint: float | None


class ProfileParser(ABC):
    """Best-effort parser for freelancer profile text."""

    @abstractmethod
    def parse(self, profile_text: str) -> ParsedProfile | None:
        """Return parsed profile or None when parsing is unavailable/failed."""
        ...
