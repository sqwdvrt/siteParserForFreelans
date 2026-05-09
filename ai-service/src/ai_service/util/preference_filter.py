"""User-preference pre-filter applied before pgvector ANN search."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ai_service.domain.job import Job
from ai_service.domain.user import User
from ai_service.util.profile_structurer import extract_structured_profile

_BUDGET_RE = re.compile(r"(\d[\d\s.,]*)(?:\s*([kк]))?", re.IGNORECASE)
_YEARS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*\+?\s*(?:лет|года|год|years?)", re.IGNORECASE)
_MAX_EXPERIENCE_YEARS = 30  # защита от абсурдных значений типа "2025 лет"
_TECH_TOKENS: tuple[str, ...] = (
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
    "postgres",
    "postgresql",
    "mysql",
    "redis",
    "docker",
    "kubernetes",
)
_TECH_RE_BY_TOKEN = {
    token: re.compile(rf"(?<!\w){re.escape(token)}(?!\w)", re.IGNORECASE)
    for token in _TECH_TOKENS
}

PREFERENCE_FILTER_REASON_SOURCE = "source"
PREFERENCE_FILTER_REASON_WORK_TYPE = "work_type"
PREFERENCE_FILTER_REASON_STACK = "stack"
PREFERENCE_FILTER_REASON_EXPERIENCE = "experience"
PREFERENCE_FILTER_REASON_EXCLUDE_KEYWORD = "exclude_keyword"
PREFERENCE_FILTER_REASON_INCLUDE_KEYWORD = "include_keyword"
PREFERENCE_FILTER_REASON_BUDGET = "budget"


@dataclass(slots=True)
class PreferenceFilterResult:
    """Result of pre-filtering users with explicit reasons for excluded users."""

    passed: list[User]
    filtered_user_reasons: dict[int, set[str]]


def parse_budget_amount(raw_budget: str) -> float | None:
    """Extract a numeric budget from free-form text.

    Returns None when parsing fails so the caller can keep the user instead of
    accidentally filtering on malformed budget strings.
    """
    text = str(raw_budget or "").strip().replace("\xa0", " ")
    if not text:
        return None

    match = _BUDGET_RE.search(text)
    if match is None:
        return None

    number = _normalize_budget_number(match.group(1))
    if number is None:
        return None
    suffix = (match.group(2) or "").strip().lower()
    if suffix in {"k", "к"}:
        number *= 1000.0
    return number


def apply_preference_filter(
    job: Job,
    users: list[User],
    *,
    classification: dict[str, object] | None = None,
) -> list[User]:
    """Keep only users whose explicit preferences allow *job*."""
    return evaluate_preference_filter(job, users, classification=classification).passed


def evaluate_preference_filter(
    job: Job,
    users: list[User],
    *,
    classification: dict[str, object] | None = None,
) -> PreferenceFilterResult:
    """Return passed users plus explicit exclusion reasons per user."""
    if not users:
        return PreferenceFilterResult(passed=[], filtered_user_reasons={})

    source = str(job.source or "").strip().lower()
    haystack = f"{job.title or ''}\n{job.description or ''}".casefold()
    job_stack = _extract_job_stack(haystack, classification)
    job_type = _extract_job_type(haystack, classification)
    required_experience_years = _extract_required_experience_years(haystack, classification)
    budget_amount = parse_budget_amount(job.budget)
    passed: list[User] = []
    filtered_user_reasons: dict[int, set[str]] = {}

    def mark_filtered(user_id: int, reason: str) -> None:
        filtered_user_reasons.setdefault(int(user_id), set()).add(reason)

    for user in users:
        prefs = user.preferences
        structured = extract_structured_profile(user.profile_text or "")
        preferred_sources = tuple(str(item).strip().lower() for item in prefs.preferred_sources if str(item).strip())
        if preferred_sources and source not in preferred_sources:
            mark_filtered(user.id, PREFERENCE_FILTER_REASON_SOURCE)
            continue

        if (
            structured.work_type is not None
            and job_type is not None
            and structured.work_type != job_type
        ):
            mark_filtered(user.id, PREFERENCE_FILTER_REASON_WORK_TYPE)
            continue
        if structured.stack and job_stack and not (set(structured.stack) & job_stack):
            mark_filtered(user.id, PREFERENCE_FILTER_REASON_STACK)
            continue
        if (
            structured.experience_years is not None
            and required_experience_years is not None
            and structured.experience_years < required_experience_years
        ):
            mark_filtered(user.id, PREFERENCE_FILTER_REASON_EXPERIENCE)
            continue

        excluded = False
        for keyword in prefs.exclude_keywords:
            if _keyword_matches(keyword, haystack):
                excluded = True
                break
        if excluded:
            mark_filtered(user.id, PREFERENCE_FILTER_REASON_EXCLUDE_KEYWORD)
            continue

        include_keywords = [str(kw or "").strip() for kw in prefs.include_keywords if str(kw or "").strip()]
        if include_keywords and not any(_keyword_matches(kw, haystack) for kw in include_keywords):
            mark_filtered(user.id, PREFERENCE_FILTER_REASON_INCLUDE_KEYWORD)
            continue

        if budget_amount is not None:
            min_budget = (
                float(prefs.min_budget)
                if prefs.min_budget is not None
                else structured.desired_budget_min
            )
            if min_budget is not None and budget_amount < min_budget:
                mark_filtered(user.id, PREFERENCE_FILTER_REASON_BUDGET)
                continue
            if prefs.max_budget is not None and budget_amount > float(prefs.max_budget):
                mark_filtered(user.id, PREFERENCE_FILTER_REASON_BUDGET)
                continue

        passed.append(user)
    return PreferenceFilterResult(passed=passed, filtered_user_reasons=filtered_user_reasons)


def _normalize_budget_number(raw_number: str) -> float | None:
    compact = re.sub(r"\s+", "", str(raw_number or ""))
    if not compact:
        return None

    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    elif compact.count(",") == 1 and compact.count(".") == 0:
        compact = compact.replace(",", ".")
    else:
        compact = compact.replace(",", "")

    try:
        return float(compact)
    except ValueError:
        return None


def _keyword_matches(raw_keyword: str, haystack: str) -> bool:
    normalized = str(raw_keyword or "").strip().casefold()
    if not normalized:
        return False

    parts = [re.escape(part) for part in re.split(r"\s+", normalized) if part]
    if not parts:
        return False

    separator_pattern = r"\s+".join(parts)
    pattern = re.compile(rf"(?<!\w){separator_pattern}(?!\w)", re.IGNORECASE)
    return pattern.search(haystack) is not None


def _extract_job_stack(haystack: str, classification: dict[str, object] | None) -> set[str]:
    stack: set[str] = set()
    if classification:
        technologies = classification.get("technologies")
        if isinstance(technologies, list):
            for raw in technologies:
                token = str(raw or "").strip().lower()
                if token:
                    stack.add(token)
    for token, token_re in _TECH_RE_BY_TOKEN.items():
        if token_re.search(haystack):
            stack.add(token)
    return stack


def _extract_job_type(haystack: str, classification: dict[str, object] | None) -> str | None:
    if classification:
        project_type = str(classification.get("project_type") or "").strip().lower()
        if project_type in {"web", "mobile", "bot"}:
            return project_type
    if "mobile" in haystack or "android" in haystack or "ios" in haystack:
        return "mobile"
    if "telegram" in haystack or "бот" in haystack or "bot" in haystack:
        return "bot"
    if "backend" in haystack or "frontend" in haystack or "web" in haystack or "веб" in haystack:
        return "web"
    return None


def _extract_required_experience_years(haystack: str, classification: dict[str, object] | None) -> float | None:
    if classification:
        seniority = str(classification.get("seniority") or "").strip().lower()
        if seniority == "junior":
            return 1.0
        if seniority == "middle":
            return 3.0
        if seniority == "senior":
            return 5.0
    candidates: list[float] = []
    for m in _YEARS_RE.finditer(haystack):
        raw = m.group(1).replace(",", ".")
        try:
            val = float(raw)
        except ValueError:
            continue
        if val <= _MAX_EXPERIENCE_YEARS:
            candidates.append(val)
    if not candidates:
        return None
    return max(candidates)
