"""FallbackClassifier: primary (Ollama) → fallback (RuleBased) при пустом результате."""

from __future__ import annotations

import logging

from ai_service.port.classifier import ClassificationResult, Classifier

logger = logging.getLogger(__name__)

# Ключевые поля, определяющие «содержательный» результат
_MEANINGFUL_FIELDS = {"project_type", "seniority", "complexity"}


def _is_empty_result(result: ClassificationResult) -> bool:
    """Результат считается пустым, если не заполнено ни одно из ключевых полей."""
    return not any(field in result for field in _MEANINGFUL_FIELDS)


class FallbackClassifier(Classifier):
    """Запускает primary; если результат пуст — использует fallback.

    Стратегия merge: поля из primary имеют приоритет,
    пропущенные поля дополняются из fallback.
    """

    def __init__(self, primary: Classifier, fallback: Classifier) -> None:
        self._primary = primary
        self._fallback = fallback

    def classify(self, text: str) -> ClassificationResult:
        try:
            primary_result = self._primary.classify(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("primary classifier raised: %s", exc)
            primary_result = {}

        if _is_empty_result(primary_result):
            logger.debug("primary result empty, using fallback classifier")
            return self._fallback.classify(text)

        # Дополняем пропущенные поля из fallback
        try:
            fallback_result = self._fallback.classify(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fallback classifier raised: %s", exc)
            return primary_result

        merged: ClassificationResult = {**fallback_result, **primary_result}
        return merged
