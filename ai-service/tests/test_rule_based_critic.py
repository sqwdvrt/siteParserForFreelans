"""Tests for RuleBasedCriticAgent."""

from __future__ import annotations

from ai_service.adapter.critic import RuleBasedCriticAgent
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User


def _selection(count: int) -> list[RankedJob]:
    return [
        RankedJob(
            job_id=index + 1,
            title=f"Job {index + 1}",
            why_it_fits="Profile match.",
            rank=index + 1,
            actor_confidence=0.8,
        )
        for index in range(count)
    ]


def _user() -> User:
    return User(id=1, telegram_id=123, profile_text="Python backend", embedding=None)


def test_evaluate_empty_selection() -> None:
    critic = RuleBasedCriticAgent()

    result = critic.evaluate(user=_user(), selection=[])

    assert result.score == 0.0
    assert "few options" in result.critique.lower()


def test_evaluate_three_jobs_passes_threshold() -> None:
    critic = RuleBasedCriticAgent()

    result = critic.evaluate(user=_user(), selection=_selection(3))

    assert result.score == 6.0
    assert "enough" in result.critique.lower()


def test_evaluate_score_capped_at_ten() -> None:
    critic = RuleBasedCriticAgent()

    result = critic.evaluate(user=_user(), selection=_selection(10))

    assert result.score == 10.0
