"""Helpers to extract structured signals from user profile_text."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SKILLS_LINE_RE = re.compile(r"(?:навыки|skills?)\s*:\s*([^\n.]+)", re.IGNORECASE)
_SPECIALIZATION_LINE_RE = re.compile(
    r"(?:специализац(?:ия|ии)|specialization)\s*:\s*([^\n.]+)",
    re.IGNORECASE,
)
_RATE_LINE_RE = re.compile(
    r"(?:желаем[а-я]+(?:\s+ставк[а-я]+)?|rate)\s*:\s*([^\n.]+)",
    re.IGNORECASE,
)
_YEARS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*\+?\s*(?:лет|года|год|years?)", re.IGNORECASE)

_KNOWN_TECHS: tuple[str, ...] = (
    "python",
    "go",
    "node.js",
    "nodejs",
    "java",
    "c#",
    "php",
    "rust",
    "react",
    "vue",
    "angular",
    "typescript",
    "javascript",
    "swift",
    "kotlin",
    "flutter",
    "django",
    "fastapi",
    "laravel",
    "postgresql",
    "postgres",
    "mysql",
    "redis",
    "docker",
    "kubernetes",
)
_TECH_RE_BY_TOKEN = {
    token: re.compile(rf"(?<!\w){re.escape(token)}(?!\w)", re.IGNORECASE)
    for token in _KNOWN_TECHS
}
_RATE_MIN_RE = re.compile(r"(\d[\d\s.,]*)")
_BOT_RE = re.compile(r"(?:\bbot\b|telegram|телеграм|\bбот\w*)", re.IGNORECASE)
_MOBILE_RE = re.compile(r"(?:\bmobile\b|мобиль)", re.IGNORECASE)
_WEB_RE = re.compile(r"(?:\bbackend\b|\bfrontend\b|\bweb\b|веб|\bdevops\b|\bdata\b|\bqa\b)", re.IGNORECASE)


@dataclass(frozen=True)
class StructuredProfile:
    stack: tuple[str, ...] = ()
    experience_years: float | None = None
    desired_budget_min: float | None = None
    work_type: str | None = None  # web | mobile | bot | None


def extract_structured_profile(profile_text: str) -> StructuredProfile:
    text = " ".join(str(profile_text or "").replace("\xa0", " ").split())
    if not text:
        return StructuredProfile()

    stack = _extract_stack(text)
    experience_years = _extract_experience_years(text)
    desired_budget_min = _extract_desired_budget_min(text)
    work_type = _extract_work_type(text)
    return StructuredProfile(
        stack=stack,
        experience_years=experience_years,
        desired_budget_min=desired_budget_min,
        work_type=work_type,
    )


def build_structured_profile_text(profile_text: str) -> str:
    """Return normalized text with explicit fields for embedding/rerank."""
    raw = " ".join(str(profile_text or "").replace("\xa0", " ").split())
    if not raw:
        return ""

    structured = extract_structured_profile(raw)
    lines: list[str] = ["Профиль фрилансера"]
    if structured.work_type:
        lines.append(f"Тип работы: {structured.work_type}")
    if structured.experience_years is not None:
        lines.append(f"Опыт (лет): {structured.experience_years:.1f}")
    if structured.stack:
        lines.append(f"Стек: {', '.join(structured.stack)}")
    if structured.desired_budget_min is not None:
        lines.append(f"Желаемый бюджет от: {int(structured.desired_budget_min)} RUB")
    lines.append(f"Описание: {raw}")
    return "\n".join(lines)


def _extract_stack(text: str) -> tuple[str, ...]:
    from_line = _extract_stack_from_skills_line(text)
    if from_line:
        return from_line
    return tuple(token for token, token_re in _TECH_RE_BY_TOKEN.items() if token_re.search(text))


def _extract_stack_from_skills_line(text: str) -> tuple[str, ...]:
    match = _SKILLS_LINE_RE.search(text)
    if match is None:
        return ()
    raw_skills = [item.strip().lower() for item in re.split(r"[,;/|]", match.group(1))]
    seen: set[str] = set()
    normalized: list[str] = []
    for item in raw_skills:
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


def _extract_experience_years(text: str) -> float | None:
    lower = text.lower()
    if "lead" in lower or "architect" in lower:
        return 8.0
    if "senior" in lower:
        return 6.0
    if "middle" in lower:
        return 3.5
    if "junior" in lower:
        return 1.5
    match = _YEARS_RE.search(text)
    if match is None:
        return None
    raw = match.group(1).replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_desired_budget_min(text: str) -> float | None:
    rate_match = _RATE_LINE_RE.search(text)
    if rate_match is None:
        return None
    candidate = rate_match.group(1)
    if "не указывать" in candidate.lower():
        return None
    number_match = _RATE_MIN_RE.search(candidate)
    if number_match is None:
        return None
    compact = re.sub(r"\s+", "", number_match.group(1))
    compact = compact.replace(",", ".")
    if compact.count(".") > 1:
        compact = compact.replace(".", "")
    try:
        return float(compact)
    except ValueError:
        return None


def _extract_work_type(text: str) -> str | None:
    lower = text.lower()
    line_match = _SPECIALIZATION_LINE_RE.search(text)
    if line_match is not None:
        lower = line_match.group(1).lower()
    if _MOBILE_RE.search(lower):
        return "mobile"
    if _BOT_RE.search(lower):
        return "bot"
    if _WEB_RE.search(lower):
        return "web"
    return None
