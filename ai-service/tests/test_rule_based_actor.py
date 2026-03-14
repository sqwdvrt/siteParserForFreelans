"""Tests for RuleBasedActorAgent."""

from __future__ import annotations

from ai_service.adapter.actor import RuleBasedActorAgent
from ai_service.domain.job import Job
from ai_service.domain.user import User


def _job(job_id: int, title: str) -> Job:
    return Job(id=job_id, title=title, description=f"{title} description", raw_html="<p>raw</p>")


def _user() -> User:
    return User(id=1, telegram_id=123, profile_text="Python backend", embedding=None)


def test_select_limits_and_preserves_order() -> None:
    actor = RuleBasedActorAgent()
    candidates = [_job(1, "A"), _job(2, "B"), _job(3, "C"), _job(4, "D")]

    result = actor.select(user=_user(), candidates=candidates, max_jobs=3)

    assert len(result) == 3
    assert [item.job_id for item in result] == [1, 2, 3]
    assert [item.rank for item in result] == [1, 2, 3]
    assert result[0].actor_confidence == 1.0
    assert result[1].actor_confidence == 0.9
    assert result[2].actor_confidence == 0.8
    assert all(item.why_it_fits for item in result)


def test_select_returns_empty_for_non_positive_limit() -> None:
    actor = RuleBasedActorAgent()
    candidates = [_job(1, "A"), _job(2, "B")]

    assert actor.select(user=_user(), candidates=candidates, max_jobs=0) == []
    assert actor.select(user=_user(), candidates=candidates, max_jobs=-1) == []


def test_select_ignores_critique_and_still_returns_results() -> None:
    actor = RuleBasedActorAgent()
    candidates = [_job(1, "A")]

    result = actor.select(
        user=_user(),
        candidates=candidates,
        max_jobs=1,
        critique="Need more Python jobs",
    )

    assert len(result) == 1
    assert result[0].job_id == 1


def test_explain_batch_returns_one_explanation_per_candidate() -> None:
    actor = RuleBasedActorAgent()
    candidates = [_job(1, "A"), _job(2, "B")]

    result = actor.explain_batch(user=_user(), candidates=candidates)

    assert result == [
        "Соответствует вашему профилю по схожести.",
        "Соответствует вашему профилю по схожести.",
    ]
