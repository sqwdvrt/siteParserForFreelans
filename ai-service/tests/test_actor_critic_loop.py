"""Tests for ActorCriticLoop orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock

from ai_service.domain.critic_result import CriticResult
from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.usecase.actor_critic_loop import ActorCriticConfig, ActorCriticLoop


def _user() -> User:
    return User(id=1, telegram_id=123, profile_text="Python backend", embedding=[0.1, 0.2])


def _candidates() -> list[tuple[Job, float]]:
    return [
        (Job(id=1, title="A", description="Desc A", raw_html="<p>a</p>"), 0.90),
        (Job(id=2, title="B", description="Desc B", raw_html="<p>b</p>"), 0.85),
        (Job(id=3, title="C", description="Desc C", raw_html="<p>c</p>"), 0.80),
    ]


def _selection(job_ids: list[int]) -> list[RankedJob]:
    return [
        RankedJob(
            job_id=job_id,
            title=f"Job {job_id}",
            why_it_fits="Good match.",
            rank=index + 1,
            actor_confidence=0.9 - (index * 0.1),
        )
        for index, job_id in enumerate(job_ids)
    ]


def test_passes_on_first_attempt_when_score_gte_threshold() -> None:
    actor = MagicMock()
    critic = MagicMock()
    actor.select.return_value = _selection([1, 2, 3])
    critic.evaluate.return_value = CriticResult(score=7.0, critique="Looks good.")

    result = ActorCriticLoop(actor, critic).run(_user(), _candidates())

    assert result.passed is True
    assert result.attempts == 1
    assert len(result.selection) == 3
    assert result.final_score == 7.0
    actor.select.assert_called_once()
    critic.evaluate.assert_called_once()


def test_passes_on_third_attempt() -> None:
    actor = MagicMock()
    critic = MagicMock()
    actor.select.side_effect = [
        _selection([1]),
        _selection([1, 2]),
        _selection([1, 2, 3]),
    ]
    critic.evaluate.side_effect = [
        CriticResult(score=3.0, critique="Need more relevance."),
        CriticResult(score=4.5, critique="Still weak diversity."),
        CriticResult(score=6.0, critique="Acceptable now."),
    ]
    loop = ActorCriticLoop(actor, critic, ActorCriticConfig(max_attempts=3, score_threshold=5.0))

    result = loop.run(_user(), _candidates())

    assert result.passed is True
    assert result.attempts == 3
    assert result.final_score == 6.0
    assert len(result.selection) == 3
    assert actor.select.call_count == 3
    assert critic.evaluate.call_count == 3


def test_exhausted_returns_best_selection() -> None:
    actor = MagicMock()
    critic = MagicMock()
    sel1 = _selection([1])
    sel2 = _selection([1, 2])
    sel3 = _selection([1, 2, 3])
    actor.select.side_effect = [sel1, sel2, sel3]
    critic.evaluate.side_effect = [
        CriticResult(score=2.0, critique="Too narrow."),
        CriticResult(score=3.0, critique="Improve personalization."),
        CriticResult(score=4.0, critique="Closer, but below threshold."),
    ]
    loop = ActorCriticLoop(actor, critic, ActorCriticConfig(max_attempts=3, score_threshold=5.0))

    result = loop.run(_user(), _candidates())

    assert result.passed is False
    assert result.attempts == 3
    assert result.final_score == 4.0
    assert result.selection == sel3


def test_empty_selection_stops_loop() -> None:
    actor = MagicMock()
    critic = MagicMock()
    actor.select.return_value = []
    loop = ActorCriticLoop(actor, critic)

    result = loop.run(_user(), _candidates())

    assert result.passed is False
    assert result.selection == []
    assert result.final_score == 0.0
    assert result.attempts == 1
    assert critic.evaluate.call_count == 0


def test_critique_passed_to_next_actor_call() -> None:
    actor = MagicMock()
    critic = MagicMock()
    actor.select.side_effect = [_selection([1]), _selection([1, 2])]
    critic.evaluate.side_effect = [
        CriticResult(score=3.0, critique="Need more Python jobs"),
        CriticResult(score=6.0, critique="Good enough"),
    ]
    loop = ActorCriticLoop(actor, critic)

    result = loop.run(_user(), _candidates())

    assert result.passed is True
    assert actor.select.call_count == 2
    second_call_kwargs = actor.select.call_args_list[1].kwargs
    assert second_call_kwargs["critique"] == "Need more Python jobs"


def test_empty_candidates_returns_empty_without_agent_calls() -> None:
    actor = MagicMock()
    critic = MagicMock()
    loop = ActorCriticLoop(actor, critic)

    result = loop.run(_user(), [])

    assert result.selection == []
    assert result.passed is False
    assert result.attempts == 0
    assert result.final_score == 0.0
    assert actor.select.call_count == 0
    assert critic.evaluate.call_count == 0
