"""Port: Classifier — абстракция классификации текста проекта."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypedDict


class ClassificationResult(TypedDict, total=False):
    """Схема ответа классификатора."""

    project_type: str
    seniority: str
    technologies: list[str]
    complexity: str
    budget_level: str
    is_spam: bool


class Classifier(ABC):
    """Классификатор проекта: технологии, тип, уровень, спам."""

    @abstractmethod
    def classify(self, text: str) -> ClassificationResult:
        """Классифицировать текст. Возвращает dict с полями схемы."""
        ...
