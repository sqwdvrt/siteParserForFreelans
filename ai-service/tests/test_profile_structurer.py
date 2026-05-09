from __future__ import annotations

from ai_service.util.profile_structurer import build_structured_profile_text, extract_structured_profile


def test_extract_structured_profile_from_onboarding_text() -> None:
    profile = extract_structured_profile(
        "Специализация: Backend-разработка. "
        "Уровень опыта: Senior (5+ лет). "
        "Навыки: Python, PostgreSQL, Docker. "
        "Желаемая ставка: 2 500–5 000 ₽/ч."
    )

    assert profile.work_type == "web"
    assert profile.experience_years == 6.0
    assert profile.stack == ("python", "postgresql", "docker")
    assert profile.desired_budget_min == 2500.0


def test_build_structured_profile_text_contains_explicit_fields() -> None:
    text = build_structured_profile_text(
        "Специализация: Мобильная разработка. "
        "Уровень опыта: 4 года. "
        "Навыки: Flutter, Kotlin."
    )

    assert "Тип работы: mobile" in text
    assert "Опыт (лет): 4.0" in text
    assert "Стек: flutter, kotlin" in text


def test_extract_structured_profile_does_not_treat_upper_rate_bound_as_min_budget() -> None:
    profile = extract_structured_profile(
        "Специализация: Backend-разработка. "
        "Уровень опыта: Lead / Architect. "
        "Навыки: Python, Go. "
        "Желаемая ставка: до 1 000 ₽/ч."
    )

    assert profile.work_type == "web"
    assert profile.experience_years == 8.0
    assert profile.stack == ("python", "go")
    assert profile.desired_budget_min is None
