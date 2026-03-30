"""Gemini Actor implementation."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.actor import ActorAgent
from ai_service.util.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 30
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

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


class GeminiActorAgent(ActorAgent):
    """Actor backed by Gemini API JSON output."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        *,
        breaker_failure_threshold: int = 3,
        breaker_open_interval_sec: float = 30.0,
        breaker: CircuitBreaker | None = None,
        max_retries: int = 2,
        base_delay_sec: float = 5.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_sec
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=breaker_failure_threshold,
            open_interval_sec=breaker_open_interval_sec,
        )
        self._max_retries = max_retries
        self._base_delay_sec = base_delay_sec

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
            logger.warning("actor: Gemini request skipped, circuit breaker open")
            return []
        try:
            raw = self._call_gemini(ACTOR_SYSTEM_PROMPT, prompt)
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
            logger.warning("actor: Gemini explain_batch skipped, circuit breaker open")
            return []
        try:
            raw = self._call_gemini(EXPLAIN_SYSTEM_PROMPT, prompt)
        except Exception:
            self._breaker.record_failure()
            raise
        self._breaker.record_success()
        return self._parse_explanations(raw, len(candidates))

    def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{GEMINI_API_BASE}/{self._model}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3,
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
        for attempt in range(self._max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                    data = json.loads(resp.read().decode())
                candidates = data.get("candidates", [])
                if not candidates:
                    return ""
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    return ""
                return parts[0].get("text", "")
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self._max_retries:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else self._base_delay_sec * (2.0 ** attempt)
                    logger.warning(
                        "actor: Gemini 429 rate limit, retry %d/%d after %.1fs",
                        attempt + 1,
                        self._max_retries,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise
        raise RuntimeError("unreachable")  # noqa: EM101

    def _parse_response(self, raw: str, candidates: list[Job]) -> list[RankedJob]:
        try:
            data = json.loads(raw)
            selected = data.get("selected", [])
            if not isinstance(selected, list):
                return []
        except (json.JSONDecodeError, AttributeError):
            logger.error("actor: invalid JSON from Gemini: %s", raw[:200])
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
            logger.error("actor: invalid explanations JSON from Gemini: %s", raw[:200])
            return []

        cleaned = [str(item).strip()[:500] for item in explanations]
        if len(cleaned) != expected_count:
            logger.warning(
                "actor: explain_batch length mismatch from Gemini expected=%d actual=%d",
                expected_count,
                len(cleaned),
            )
            return []
        return cleaned
