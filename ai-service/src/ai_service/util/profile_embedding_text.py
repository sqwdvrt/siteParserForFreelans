"""Helpers for building enriched user-profile embedding text."""

from __future__ import annotations


def build_embedding_text(profile_text: str, structured: dict[str, object] | None) -> str:
    parts: list[str] = []
    structured = structured or {}

    stack = [str(item).strip() for item in (structured.get("stack") or []) if str(item).strip()]
    specialization = str(structured.get("specialization") or "").strip()
    level = str(structured.get("level") or "").strip()
    raw_profile = str(profile_text or "").strip()

    if stack:
        parts.append("Технологии и стек: " + ", ".join(stack))
    if specialization and specialization != "other":
        parts.append("Специализация: " + specialization)
    if level and level != "не указан":
        parts.append("Уровень: " + level)
    if raw_profile:
        parts.append(raw_profile)
    return "\n".join(parts)
