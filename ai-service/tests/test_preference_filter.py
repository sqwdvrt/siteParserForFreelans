from __future__ import annotations

from ai_service.domain.job import Job
from ai_service.domain.user import User, UserPreferences
from ai_service.util.preference_filter import (
    PREFERENCE_FILTER_REASON_BUDGET,
    apply_preference_filter,
    evaluate_preference_filter,
    parse_budget_amount,
)


def test_parse_budget_amount_handles_plain_rubles() -> None:
    assert parse_budget_amount("5000 руб") == 5000.0


def test_parse_budget_amount_handles_suffix_k() -> None:
    assert parse_budget_amount("5k руб") == 5000.0


def test_parse_budget_amount_returns_none_on_unknown_text() -> None:
    assert parse_budget_amount("договорная цена") is None


def test_apply_preference_filter_excludes_keyword_case_insensitive() -> None:
    job = Job(id=1, title="Need Laravel backend", description="PHP support", raw_html="")
    users = [
        User(
            id=10,
            telegram_id=1,
            profile_text="python",
            embedding=None,
            preferences=UserPreferences(exclude_keywords=("laravel",)),
        ),
        User(id=20, telegram_id=2, profile_text="php", embedding=None),
    ]

    filtered = apply_preference_filter(job, users)

    assert [user.id for user in filtered] == [20]


def test_apply_preference_filter_keeps_user_when_budget_parse_fails() -> None:
    job = Job(id=1, title="Backend", description="Support", raw_html="", budget="договорная")
    users = [
        User(
            id=10,
            telegram_id=1,
            profile_text="python",
            embedding=None,
            preferences=UserPreferences(min_budget=10_000.0),
        ),
    ]

    filtered = apply_preference_filter(job, users)

    assert [user.id for user in filtered] == [10]


def test_apply_preference_filter_excludes_when_stack_not_matching() -> None:
    job = Job(id=1, title="Need Golang backend", description="Microservices in Go", raw_html="")
    users = [
        User(id=10, telegram_id=1, profile_text="Навыки: Python, Django", embedding=None),
        User(id=20, telegram_id=2, profile_text="Навыки: Go, Redis", embedding=None),
    ]

    filtered = apply_preference_filter(job, users)

    assert [user.id for user in filtered] == [20]


def test_apply_preference_filter_excludes_when_experience_too_low() -> None:
    job = Job(id=1, title="Backend", description="Требуется 5+ лет коммерческого опыта", raw_html="")
    users = [
        User(
            id=10,
            telegram_id=1,
            profile_text="Уровень опыта: Middle (2–5 лет). Навыки: Python",
            embedding=None,
        ),
        User(
            id=20,
            telegram_id=2,
            profile_text="Уровень опыта: Senior (5+ лет). Навыки: Python",
            embedding=None,
        ),
    ]

    filtered = apply_preference_filter(job, users)

    assert [user.id for user in filtered] == [20]


def test_apply_preference_filter_excludes_when_work_type_mismatch() -> None:
    job = Job(id=1, title="Backend API", description="Web backend service", raw_html="")
    users = [
        User(id=10, telegram_id=1, profile_text="Специализация: Мобильная разработка", embedding=None),
        User(id=20, telegram_id=2, profile_text="Специализация: Backend-разработка", embedding=None),
    ]

    filtered = apply_preference_filter(job, users, classification={"project_type": "web"})

    assert [user.id for user in filtered] == [20]


def test_apply_preference_filter_uses_profile_budget_when_preferences_missing() -> None:
    job = Job(id=1, title="Backend", description="Support", raw_html="", budget="800 руб")
    users = [
        User(
            id=10,
            telegram_id=1,
            profile_text="Желаемая ставка: 1 000–2 500 ₽/ч. Навыки: Python",
            embedding=None,
        ),
    ]

    filtered = apply_preference_filter(job, users)

    assert filtered == []


def test_evaluate_preference_filter_records_budget_reason() -> None:
    job = Job(id=1, title="Backend", description="Support", raw_html="", budget="800 руб")
    users = [
        User(
            id=10,
            telegram_id=1,
            profile_text="Навыки: Python",
            embedding=None,
            preferences=UserPreferences(min_budget=1000.0),
        ),
        User(
            id=20,
            telegram_id=2,
            profile_text="Навыки: Python",
            embedding=None,
            preferences=UserPreferences(min_budget=500.0),
        ),
    ]

    result = evaluate_preference_filter(job, users)

    assert [user.id for user in result.passed] == [20]
    assert PREFERENCE_FILTER_REASON_BUDGET in result.filtered_user_reasons[10]
