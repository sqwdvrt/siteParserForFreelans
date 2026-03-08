"""Gemini Critic implementation."""

from __future__ import annotations

import json
import logging
import urllib.request

from ai_service.domain.critic_result import CriticResult
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.critic import CriticAgent
from ai_service.util.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 20
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

CRITIC_SYSTEM_PROMPT = """
You are evaluating job recommendation quality for a freelancer.
Rate from 0 to 10. Respond ONLY with valid JSON.
Score guide: 0-4 = poor/irrelevant, 5-6 = acceptable, 7-8 = good, 9-10 = excellent.
""".strip()

CRITIC_USER_TEMPLATE = """
Freelancer profile:
{profile}

Recommended jobs:
{selection_block}

Rate the overall quality of this recommendation. Is it personalized? Relevant? Diverse?

Respond with JSON:
{{"score": <0.0-10.0>, "critique": "<string explanation>"}}
""".strip()


class GeminiCriticAgent(CriticAgent):
    """Critic backed by Gemini API JSON output."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
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

    def evaluate(self, user: User, selection: list[RankedJob]) -> CriticResult:
        if not selection:
            return CriticResult(score=0.0, critique="Empty selection.")

        selection_block = "\n".join(
            f"#{job.rank} [{job.title}]: {job.why_it_fits}"
            for job in selection
        )
        prompt = CRITIC_USER_TEMPLATE.format(
            profile=user.profile_text or "(no profile)",
            selection_block=selection_block,
        )
        if not self._breaker.allow_request():
            logger.warning("critic: Gemini request skipped, circuit breaker open")
            return CriticResult(score=0.0, critique="Circuit breaker open.")
        try:
            raw = self._call_gemini(CRITIC_SYSTEM_PROMPT, prompt)
        except Exception:
            self._breaker.record_failure()
            raise
        result = self._parse_response(raw)
        if result.score <= 0.0 and result.critique == "Parse error.":
            self._breaker.record_failure()
        else:
            self._breaker.record_success()
        return result

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{GEMINI_API_BASE}/{self._model}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
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
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return ""
        return parts[0].get("text", "")

    def _parse_response(self, raw: str) -> CriticResult:
        try:
            data = json.loads(raw)
            score = float(data["score"])
            score = max(0.0, min(10.0, score))
            critique = str(data.get("critique", ""))[:1000]
            return CriticResult(score=score, critique=critique)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.error("critic: invalid JSON from Gemini: %s", raw[:200])
            return CriticResult(score=0.0, critique="Parse error.")
