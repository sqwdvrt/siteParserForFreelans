"""Gemini Classifier implementation."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ai_service.port.classifier import ClassificationResult, Classifier
from ai_service.util.circuit_breaker import CircuitBreaker
from ai_service.util.sanitize import sanitize_for_classifier

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 20
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

CLASSIFIER_PROMPT = (
    "Classify this freelance project description. "
    "Reply ONLY with valid JSON, no other text.\n"
    'Schema: {"project_type": "web|mobile|bot|other", "seniority": "junior|middle|senior|unknown", '
    '"technologies": ["tech1","tech2"], "complexity": "low|medium|high", '
    '"budget_level": "low|medium|high|unknown", "is_spam": false}\n\nText:\n'
)


class GeminiClassifier(Classifier):
    """Classifier via Gemini API. Circuit breaker + fallback on error."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        breaker_failure_threshold: int = 3,
        breaker_open_interval_sec: float = 30.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_sec
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=breaker_failure_threshold,
            open_interval_sec=breaker_open_interval_sec,
        )

    def classify(self, text: str) -> ClassificationResult:
        text = sanitize_for_classifier(text or "")
        if not text:
            return {"is_spam": False}
        if not self._breaker.allow_request():
            logger.warning("Gemini classify skipped: circuit breaker open")
            return {}

        try:
            url = f"{GEMINI_API_BASE}/{self._model}:generateContent"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": CLASSIFIER_PROMPT + text}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                },
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                data = json.loads(resp.read().decode())
            candidates = data.get("candidates", [])
            if not candidates:
                self._breaker.record_success()
                return {}
            parts = candidates[0].get("content", {}).get("parts", [])
            raw = parts[0].get("text", "") if parts else ""
            result = self._parse_response(raw)
            self._breaker.record_success()
            return result
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            self._breaker.record_failure()
            logger.warning("Gemini classify failed: %s", e)
            return {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self._breaker.record_failure()
            logger.warning("Gemini classify parse error: %s", e)
            return {}

    def _parse_response(self, raw: str) -> ClassificationResult:
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            return {}

        result: ClassificationResult = {}
        if "project_type" in data and isinstance(data["project_type"], str):
            result["project_type"] = data["project_type"][:50]
        if "seniority" in data and isinstance(data["seniority"], str):
            result["seniority"] = data["seniority"][:50]
        if "technologies" in data and isinstance(data["technologies"], list):
            result["technologies"] = [
                str(t)[:50] for t in data["technologies"][:20]
                if isinstance(t, (str, int, float))
            ]
        if "complexity" in data and isinstance(data["complexity"], str):
            result["complexity"] = data["complexity"][:50]
        if "budget_level" in data and isinstance(data["budget_level"], str):
            result["budget_level"] = data["budget_level"][:50]
        if "is_spam" in data and isinstance(data["is_spam"], bool):
            result["is_spam"] = data["is_spam"]
        elif "is_spam" in data:
            result["is_spam"] = False
        return result
