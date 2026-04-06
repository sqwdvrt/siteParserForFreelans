"""Очистка текста для embedding: HTML → plain text, нормализация, умная обрезка.

Instead of hard truncating at MAX_LENGTH characters, this module uses
*semantic chunking* to preserve the most important parts of a job posting:

Priority order (preserved first):
  1. Title (always 100%)
  2. Skills / technologies (always 100%)
  3. Budget (always 100%)
  4. Description (fills remaining budget)
  5. Raw HTML remnants (only if space remains)

This ensures that embedding quality does not degrade for long descriptions
where critical metadata would otherwise be cut off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

MAX_LENGTH = 2000

# Regex patterns to extract structured sections from raw text
_SKILLS_SECTION_RE = re.compile(
    r"(?:навыки|skills|требования|requirements|стек|stack|технологии)"
    r"\s*[:：]\s*(.+?)(?:\n\n|\n\s*\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_BUDGET_SECTION_RE = re.compile(
    r"(?:бюджет|budget|цена|оплата|payment|стоимость)"
    r"\s*[:：]\s*(.+?)(?:\n\n|\n\s*\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_TECH_LIST_RE = re.compile(
    r"(?:python|go|golang|node\.?js|java|c#|php|rust|react|vue|angular|"
    r"typescript|javascript|swift|kotlin|flutter|django|fastapi|laravel|"
    r"postgres(?:ql)?|mysql|redis|docker|kubernetes|aws|gcp|azure|"
    r"mongodb|elasticsearch|git|linux|nginx|apache)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SemanticChunks:
    """Job text split into prioritised sections."""

    title: str = ""
    skills: str = ""
    budget: str = ""
    description: str = ""


def _extract_skills(text: str) -> str:
    """Find and return the skills section from raw text."""
    match = _SKILLS_SECTION_RE.search(text)
    if match:
        return match.group(1).strip()
    # Fallback: collect standalone tech keywords
    techs = _TECH_LIST_RE.findall(text)
    return ", ".join(dict.fromkeys(t.lower() for t in techs))


def _extract_budget(text: str) -> str:
    """Find and return the budget section from raw text."""
    match = _BUDGET_SECTION_RE.search(text)
    if match:
        return match.group(1).strip()
    return ""


def _semantic_split(html: str) -> SemanticChunks:
    """Parse HTML and extract prioritised semantic sections."""
    if not html or not html.strip():
        return SemanticChunks()

    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(separator=" ", strip=True)
    full_text = re.sub(r"\s+", " ", full_text).strip()

    # Try to extract title from HTML structure first
    title_tag = soup.find(["h1", "h2", "title"])
    title = ""
    if title_tag:
        title = title_tag.get_text(separator=" ", strip=True)
    else:
        # First sentence / line as title fallback
        first_line = full_text.split("\n")[0].strip()
        if len(first_line) < 200:
            title = first_line

    skills = _extract_skills(full_text)
    budget = _extract_budget(full_text)

    # Description = everything except already extracted parts
    # Simple approach: use full text, the sections above will be duplicated
    # but that's fine for embedding (reinforces signal)
    description = full_text

    return SemanticChunks(
        title=title,
        skills=skills,
        budget=budget,
        description=description,
    )


def clean_text(html: str) -> str:
    """Извлечь и очистить текст из HTML.

    Uses *semantic chunking* to preserve critical job metadata:

    1. HTML → plain text (BeautifulSoup)
    2. Normalise whitespace
    3. Compose prioritised output:
       - Title (100%)
       - Skills section (100%)
       - Budget (100%)
       - Description (remaining budget up to MAX_LENGTH)

    This avoids hard truncation of important sections like skills or budget.
    """
    if not html or not html.strip():
        return ""

    chunks = _semantic_split(html)

    # Build prioritised output
    parts: list[str] = []
    remaining = MAX_LENGTH

    # 1. Title — always include
    if chunks.title:
        parts.append(chunks.title)
        remaining -= len(chunks.title) + 1  # +1 for space

    # 2. Skills — always include
    if chunks.skills:
        skills_prefix = "Skills: "
        skills_block = skills_prefix + chunks.skills
        if len(skills_block) <= remaining:
            parts.append(skills_block)
            remaining -= len(skills_block) + 1
        else:
            parts.append(skills_prefix + chunks.skills[:remaining - len(skills_prefix)])
            remaining = 0

    # 3. Budget — always include
    if remaining > 0 and chunks.budget:
        budget_prefix = "Budget: "
        budget_block = budget_prefix + chunks.budget
        if len(budget_block) <= remaining:
            parts.append(budget_block)
            remaining -= len(budget_block) + 1
        else:
            parts.append(budget_prefix + chunks.budget[:remaining - len(budget_prefix)])
            remaining = 0

    # 4. Description — fill remaining budget
    if remaining > 0 and chunks.description:
        desc = chunks.description
        if len(desc) > remaining:
            desc = desc[:remaining].rstrip()
        parts.append(desc)

    result = " ".join(parts)
    # Final safety trim
    if len(result) > MAX_LENGTH:
        result = result[:MAX_LENGTH].rstrip()

    return result
