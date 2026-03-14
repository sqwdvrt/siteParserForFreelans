"""Ollama Actor implementation."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.actor import ActorAgent
from ai_service.util.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 45
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

ACTOR_SYSTEM_PROMPT = """
You are a job matching expert. Given a freelancer's profile and job listings,
select the most relevant jobs. Respond ONLY with valid JSON.
""".strip()

ACTOR_USER_TEMPLATE = """
Freelancer profile:
{profile}

Candidate jobs:
{jobs_block}

Select top {max_jobs} most relevant jobs. For each, explain why it fits this freelancer.
{critique_block}

Respond with JSON:
{{"selected": [{{"job_id": <int>, "rank": <int 1=best>, "why_it_fits": "<string>", "confidence": <0.0-1.0>}}]}}
""".strip()

EXPLAIN_SYSTEM_PROMPT = """
You are a job matching expert. Given a freelancer's profile and preselected jobs,
write one concise why_it_fits explanation per job. Respond ONLY with valid JSON.
""".strip()

EXPLAIN_USER_TEMPLATE = """
Freelancer profile:
{profile}

Preselected jobs in fixed order:
{jobs_block}

Write exactly {job_count} short explanations in the same order as the jobs above.

Respond with JSON:
{{"explanations": ["...", "..."]}}
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


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class OllamaActorAgent(ActorAgent):
    """Actor backed by Ollama JSON output."""

    def __init__(
        self,
        base_url: str,
        model: str,
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

    def select(
        self,
        user: User,
        candidates: list[Job],
        max_jobs: int = 5,
        critique: str | None = None,
    ) -> list[RankedJob]:
        if not candidates or max_jobs <= 0:
            return []

        jobs_block = "\n".join(
            f"{job.id}. [{job.title}] {(job.description or '')[:300]}"
            for job in candidates
        )
        critique_block = f"\nPrevious feedback to address: {critique}" if critique else ""
        prompt = ACTOR_USER_TEMPLATE.format(
            profile=user.profile_text or "(no profile)",
            jobs_block=jobs_block,
            max_jobs=min(max_jobs, len(candidates)),
            critique_block=critique_block,
        )
        if not self._breaker.allow_request():
            logger.warning("actor: Ollama request skipped, circuit breaker open")
            return []
        try:
            raw = self._call_ollama(prompt)
        except Exception:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return self._parse_response(raw, candidates)

    def explain_batch(
        self,
        user: User,
        candidates: list[Job],
    ) -> list[str]:
        if not candidates:
            return []

        jobs_block = "\n".join(
            f"{index + 1}. [{job.title}] {(job.description or '')[:300]}"
            for index, job in enumerate(candidates)
        )
        prompt = EXPLAIN_USER_TEMPLATE.format(
            profile=user.profile_text or "(no profile)",
            jobs_block=jobs_block,
            job_count=len(candidates),
        )
        if not self._breaker.allow_request():
            logger.warning("actor: Ollama explain_batch skipped, circuit breaker open")
            return []
        try:
            raw = self._call_ollama(prompt, system_prompt=EXPLAIN_SYSTEM_PROMPT)
        except Exception:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return self._parse_explanations(raw, len(candidates))

    def _call_ollama(self, prompt: str, *, system_prompt: str = ACTOR_SYSTEM_PROMPT) -> str:
        target_url = f"{self._base_url}/api/generate"
        self._validate_ollama_url(target_url)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": system_prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.3},
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

    def _parse_response(self, raw: str, candidates: list[Job]) -> list[RankedJob]:
        try:
            data = json.loads(raw)
            selected = data.get("selected", [])
            if not isinstance(selected, list):
                return []
        except (json.JSONDecodeError, AttributeError):
            logger.error("actor: invalid JSON from Ollama: %s", raw[:200])
            return []

        job_map = {job.id: job for job in candidates}
        result: list[RankedJob] = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            job_id = _to_int(item.get("job_id"), -1)
            if job_id not in job_map:
                continue
            rank = _to_int(item.get("rank"), 99)
            confidence = _to_float(item.get("confidence"), 0.5)
            confidence = max(0.0, min(1.0, confidence))
            result.append(
                RankedJob(
                    job_id=job_id,
                    title=job_map[job_id].title,
                    why_it_fits=str(item.get("why_it_fits", ""))[:500],
                    rank=rank,
                    actor_confidence=confidence,
                    final_score=job_map[job_id].final_score or job_map[job_id].match_score,
                    ranker_version=job_map[job_id].ranker_version,
                    reason_codes=tuple(job_map[job_id].reason_codes or ()),
                )
            )
        return sorted(result, key=lambda job: job.rank)

    def _parse_explanations(self, raw: str, expected_count: int) -> list[str]:
        try:
            data = json.loads(raw)
            explanations = data.get("explanations", [])
            if not isinstance(explanations, list):
                return []
        except (json.JSONDecodeError, AttributeError):
            logger.error("actor: invalid explanations JSON from Ollama: %s", raw[:200])
            return []

        cleaned = [str(item).strip()[:500] for item in explanations]
        if len(cleaned) != expected_count:
            logger.warning(
                "actor: explain_batch length mismatch from Ollama expected=%d actual=%d",
                expected_count,
                len(cleaned),
            )
            return []
        return cleaned
