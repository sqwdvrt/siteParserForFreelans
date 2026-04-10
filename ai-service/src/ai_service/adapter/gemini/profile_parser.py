"""Gemini-powered structured profile parser."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ai_service.port.profile_parser import ParsedProfile, ProfileParser

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 20
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PROFILE_PARSE_PROMPT = """
Из текста профиля фрилансера извлеки структуру.
Верни строго JSON без пояснений:
{{
  "stack": ["технология1", "технология2"],
  "specialization": "backend|frontend|mobile|design|devops|other",
  "level": "junior|middle|senior|не указан",
  "preferred_work_type": "remote|office|any",
  "min_budget_hint": число или null
}}

Правила:
- stack: только конкретные технологии и фреймворки, не общие слова
- specialization: выбрать одно наиболее подходящее
- level: определить по контексту, если не указан явно — "не указан"
- min_budget_hint: если упомянута желаемая ставка/бюджет — число,
  иначе null

Профиль:
{profile_text}
"""

_VALID_SPECIALIZATIONS = {"backend", "frontend", "mobile", "design", "devops", "other"}
_VALID_LEVELS = {"junior", "middle", "senior", "не указан"}
_VALID_WORK_TYPES = {"remote", "office", "any"}


class GeminiProfileParser(ProfileParser):
    """Profile parser via Gemini API. Returns None on any failure."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_sec

    def parse(self, profile_text: str) -> ParsedProfile | None:
        profile_text = " ".join(str(profile_text or "").replace("\xa0", " ").split())
        if not profile_text:
            return None
        try:
            url = f"{GEMINI_API_BASE}/{self._model}:generateContent"
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": PROFILE_PARSE_PROMPT.format(profile_text=profile_text)}],
                    }
                ],
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
                logger.warning("profile parse returned empty candidates")
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            raw = parts[0].get("text", "") if parts else ""
            return self._parse_response(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            logger.warning("profile parse request failed: %s", exc)
            return None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("profile parse failed: %s", exc)
            return None

    def _parse_response(self, raw: str | dict[str, Any]) -> ParsedProfile | None:
        if isinstance(raw, dict):
            data = raw
        else:
            data = json.loads(raw)

        stack = _normalize_stack(data.get("stack"))
        specialization = _normalize_enum(data.get("specialization"), _VALID_SPECIALIZATIONS, "other")
        level = _normalize_enum(data.get("level"), _VALID_LEVELS, "не указан")
        preferred_work_type = _normalize_enum(data.get("preferred_work_type"), _VALID_WORK_TYPES, "any")
        min_budget_hint = _normalize_budget(data.get("min_budget_hint"))

        return ParsedProfile(
            stack=stack,
            specialization=specialization,
            level=level,
            preferred_work_type=preferred_work_type,
            min_budget_hint=min_budget_hint,
        )


def _normalize_stack(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in raw:
        value = str(item or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _normalize_enum(raw: Any, allowed: set[str], fallback: str) -> str:
    value = str(raw or "").strip().lower()
    if value in allowed:
        return value
    return fallback


def _normalize_budget(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value
