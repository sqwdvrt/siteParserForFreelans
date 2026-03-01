"""Tests for new Actor-Critic domain models."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ai_service.domain.ac_result import ActorCriticResult
from ai_service.domain.critic_result import CriticResult
from ai_service.domain.ranked_job import RankedJob


def test_ranked_job_fields() -> None:
    job = RankedJob(
        job_id=101,
        title="Backend Python Developer",
        why_it_fits="Strong Django and PostgreSQL match.",
        rank=1,
        actor_confidence=0.91,
    )
    assert job.job_id == 101
    assert job.rank == 1
    assert job.actor_confidence == 0.91


def test_critic_result_fields() -> None:
    result = CriticResult(score=7.5, critique="Good relevance and acceptable diversity.")
    assert result.score == 7.5
    assert "relevance" in result.critique


def test_actor_critic_result_fields() -> None:
    selection = [
        RankedJob(
            job_id=1,
            title="Python job",
            why_it_fits="Python stack match.",
            rank=1,
            actor_confidence=0.8,
        )
    ]
    result = ActorCriticResult(selection=selection, final_score=6.2, attempts=2, passed=True)
    assert result.selection == selection
    assert result.final_score == 6.2
    assert result.attempts == 2
    assert result.passed is True


def test_models_are_frozen() -> None:
    ranked = RankedJob(job_id=1, title="T", why_it_fits="W", rank=1, actor_confidence=0.5)
    critic = CriticResult(score=5.0, critique="ok")
    ac = ActorCriticResult(selection=[ranked], final_score=5.0, attempts=1, passed=True)

    with pytest.raises(FrozenInstanceError):
        ranked.rank = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        critic.score = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        ac.passed = False  # type: ignore[misc]
