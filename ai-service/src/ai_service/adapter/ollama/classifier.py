"""Ollama Classifier: LLM с таймаутом и fallback."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ai_service.port.classifier import ClassificationResult, Classifier
from ai_service.util.circuit_breaker import CircuitBreaker
from ai_service.util.sanitize import sanitize_for_classifier

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 30
DEFAULT_OLLAMA_ALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "ollama",
    "host.docker.internal",
}
_HTTP_ONLY_OPENER = urllib.request.build_opener(
    urllib.request.HTTPHandler(),
    urllib.request.HTTPSHandler(),
)
DEFAULT_OLLAMA_MODEL = "llama3.2:3b-instruct-q4_K_M"


def _safe_open(request: urllib.request.Request, timeout: int):
    return _HTTP_ONLY_OPENER.open(request, timeout=timeout)

OLLAMA_PROMPT = """Classify this freelance project description. Reply ONLY with valid JSON, no other text.
Schema: {"project_type": "web|mobile|bot|other", "seniority": "junior|middle|senior|unknown", "technologies": ["tech1","tech2"], "complexity": "low|medium|high", "budget_level": "low|medium|high|unknown", "is_spam": false}

Text:
"""


class OllamaClassifier(Classifier):
    """Классификатор через Ollama. Таймаут, fallback при ошибке."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        breaker_failure_threshold: int = 3,
        breaker_open_interval_sec: float = 30.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_sec
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=breaker_failure_threshold,
            open_interval_sec=breaker_open_interval_sec,
        )

    @staticmethod
    def _validate_ollama_url(url: str) -> None:
        parsed = urllib.parse.urlsplit((url or "").strip())
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise ValueError("Ollama URL must use http:// or https://")
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError("Ollama URL must include host")
        if host not in DEFAULT_OLLAMA_ALLOWED_HOSTS:
            raise ValueError(f"Ollama URL host '{host}' is not in allowlist")

    def classify(self, text: str) -> ClassificationResult:
        text = sanitize_for_classifier(text or "")
        if not text:
            return {"is_spam": False}
        if not self._breaker.allow_request():
            logger.warning("Ollama classify skipped: circuit breaker open")
            return {}

        try:
            target_url = f"{self._base_url}/api/generate"
            self._validate_ollama_url(target_url)
            payload = {
                "model": self._model,
                "prompt": OLLAMA_PROMPT + text,
                "stream": False,
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                target_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _safe_open(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode())
            raw = data.get("response", "{}")
            if isinstance(raw, str):
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                result = self._parse_response(raw)
                self._breaker.record_success()
                return result
            self._breaker.record_success()
            return {}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
            self._breaker.record_failure()
            logger.warning("Ollama classify failed: %s", e)
            return {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self._breaker.record_failure()
            logger.warning("Ollama classify parse error: %s", e)
            return {}

    def _parse_response(self, raw: str) -> ClassificationResult:
        """Парсинг JSON от LLM. Fallback при битом JSON."""
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
