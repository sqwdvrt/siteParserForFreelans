"""Ollama Critic implementation."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from ai_service.domain.critic_result import CriticResult
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.critic import CriticAgent

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


def _safe_open(request: urllib.request.Request, timeout: int):
    return _HTTP_ONLY_OPENER.open(request, timeout=timeout)


def _strip_markdown_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    parts = text.split("```")
    if len(parts) < 2:
        return text
    body = parts[1]
    if body.startswith("json"):
        body = body[4:]
    return body.strip()


class OllamaCriticAgent(CriticAgent):
    """Critic backed by Ollama JSON output."""

    def __init__(self, base_url: str, model: str, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_sec

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
        raw = self._call_ollama(prompt)
        return self._parse_response(raw)

    def _call_ollama(self, prompt: str) -> str:
        target_url = f"{self._base_url}/api/generate"
        self._validate_ollama_url(target_url)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": CRITIC_SYSTEM_PROMPT,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2},
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
        response = data.get("response", "")
        if isinstance(response, str):
            return _strip_markdown_fence(response)
        if isinstance(response, (dict, list)):
            return json.dumps(response)
        return ""

    def _parse_response(self, raw: str) -> CriticResult:
        try:
            data = json.loads(raw)
            score = float(data["score"])
            score = max(0.0, min(10.0, score))
            critique = str(data.get("critique", ""))[:1000]
            return CriticResult(score=score, critique=critique)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.error("critic: invalid JSON: %s", raw[:200])
            return CriticResult(score=0.0, critique="Parse error.")
