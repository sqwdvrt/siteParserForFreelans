"""Tests for FallbackActorAgent and FallbackCriticAgent."""

from __future__ import annotations

from unittest.mock import MagicMock

from ai_service.adapter.actor import FallbackActorAgent
from ai_service.adapter.critic import FallbackCriticAgent
from ai_service.domain.critic_result import CriticResult
from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User


def _user() -> User:
    return User(id=1, telegram_id=123, profile_text="Python backend", embedding=None)


def _candidates() -> list[Job]:
    return [
        Job(id=1, title="A", description="Desc A", raw_html="<p>a</p>"),
        Job(id=2, title="B", description="Desc B", raw_html="<p>b</p>"),
    ]


def _selection() -> list[RankedJob]:
    return [
        RankedJob(
            job_id=1,
            title="A",
            why_it_fits="Matches profile.",
            rank=1,
            actor_confidence=0.9,
        )
    ]


def test_fallback_actor_returns_primary_when_non_empty() -> None:
    primary = MagicMock()
    fallback = MagicMock()
    primary.select.return_value = _selection()
    fallback.select.return_value = []
    agent = FallbackActorAgent(primary=primary, fallback=fallback)

    result = agent.select(user=_user(), candidates=_candidates(), max_jobs=2, critique="x")

    assert len(result) == 1
    assert result[0].job_id == 1
    primary.select.assert_called_once()
    fallback.select.assert_not_called()


def test_fallback_actor_uses_fallback_when_primary_empty() -> None:
    primary = MagicMock()
    fallback = MagicMock()
    primary.select.return_value = []
    fallback.select.return_value = _selection()
    agent = FallbackActorAgent(primary=primary, fallback=fallback)

    result = agent.select(user=_user(), candidates=_candidates(), max_jobs=1, critique=None)

    assert len(result) == 1
    primary.select.assert_called_once()
    fallback.select.assert_called_once()


def test_fallback_actor_uses_fallback_when_primary_raises() -> None:
    primary = MagicMock()
    fallback = MagicMock()
    primary.select.side_effect = RuntimeError("actor unavailable")
    fallback.select.return_value = _selection()
    agent = FallbackActorAgent(primary=primary, fallback=fallback)

    result = agent.select(user=_user(), candidates=_candidates(), max_jobs=1, critique=None)

    assert len(result) == 1
    fallback.select.assert_called_once()


def test_fallback_critic_returns_primary_result() -> None:
    primary = MagicMock()
    fallback = MagicMock()
    primary.evaluate.return_value = CriticResult(score=7.0, critique="good")
    fallback.evaluate.return_value = CriticResult(score=3.0, critique="fallback")
    agent = FallbackCriticAgent(primary=primary, fallback=fallback)

    result = agent.evaluate(user=_user(), selection=_selection())

    assert result.score == 7.0
    assert result.critique == "good"
    fallback.evaluate.assert_not_called()


def test_fallback_critic_uses_fallback_when_primary_raises() -> None:
    primary = MagicMock()
    fallback = MagicMock()
    primary.evaluate.side_effect = RuntimeError("critic unavailable")
    fallback.evaluate.return_value = CriticResult(score=5.0, critique="fallback ok")
    agent = FallbackCriticAgent(primary=primary, fallback=fallback)

    result = agent.evaluate(user=_user(), selection=_selection())

    assert result.score == 5.0
    assert "fallback" in result.critique
    fallback.evaluate.assert_called_once()
