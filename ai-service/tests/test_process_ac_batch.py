"""Tests for ProcessACBatchUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_service.domain.ac_result import ActorCriticResult
from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.usecase.process_ac_batch import ACBatch, ProcessACBatchUseCase


def _user_with_profile() -> User:
    return User(
        id=1,
        telegram_id=123,
        profile_text="Python backend",
        embedding=[0.1] * 384,
    )


def _jobs_with_scores() -> list[tuple[Job, float]]:
    return [
        (Job(id=1, title="A", description="Desc A", raw_html="<p>a</p>"), 0.9),
        (Job(id=2, title="B", description="Desc B", raw_html="<p>b</p>"), 0.8),
    ]


def _selection() -> list[RankedJob]:
    return [
        RankedJob(
            job_id=1,
            title="A",
            why_it_fits="Strong Python fit.",
            rank=1,
            actor_confidence=0.9,
        )
    ]


def test_execute_skips_when_user_has_no_profile() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(id=1, telegram_id=123, profile_text=None, embedding=[0.1] * 384)
    job_repo = MagicMock()
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[10, 20]))

    pending_repo.mark_processed.assert_called_once_with(1, [10, 20])
    ac_loop.run.assert_not_called()
    notify_queue.enqueue_batch.assert_not_called()


def test_execute_skips_when_user_has_no_embedding() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(id=1, telegram_id=123, profile_text="Has profile", embedding=None)
    job_repo = MagicMock()
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[10]))

    pending_repo.release_claim.assert_called_once_with(1, [10])
    pending_repo.mark_processed.assert_not_called()
    ac_loop.run.assert_not_called()


def test_execute_marks_processed_when_no_jobs_loaded() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = []
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])
    ac_loop.run.assert_not_called()


def test_execute_enqueues_batch_when_passed() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = _jobs_with_scores()
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    ac_loop.run.return_value = ActorCriticResult(
        selection=_selection(),
        final_score=7.5,
        attempts=1,
        passed=True,
    )
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    notify_queue.enqueue_batch.assert_called_once()
    kwargs = notify_queue.enqueue_batch.call_args.kwargs
    assert kwargs["user_id"] == 1
    assert kwargs["critic_score"] == 7.5
    assert len(kwargs["ranked_jobs"]) == 1
    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])


def test_execute_sends_best_selection_even_when_not_passed() -> None:
    """Уведомление отправляется даже если score < threshold.
    Critic используется для качества (retry), не для блокировки доставки."""
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = _jobs_with_scores()
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    ac_loop.run.return_value = ActorCriticResult(
        selection=_selection(),
        final_score=4.0,
        attempts=3,
        passed=False,
    )
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    notify_queue.enqueue_batch.assert_called_once()
    kwargs = notify_queue.enqueue_batch.call_args.kwargs
    assert kwargs["user_id"] == 1
    assert kwargs["critic_score"] == 4.0
    assert len(kwargs["ranked_jobs"]) == 1
    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])


def test_execute_skips_notification_when_queue_has_no_batch_method() -> None:
    class _QueueWithoutBatch:
        pass

    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = _jobs_with_scores()
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    ac_loop.run.return_value = ActorCriticResult(
        selection=_selection(),
        final_score=8.0,
        attempts=1,
        passed=True,
    )
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, _QueueWithoutBatch())

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])


def test_execute_propagates_ollama_timeout_and_keeps_batch_unprocessed() -> None:
    """Если Actor-Critic падает по timeout (например, Ollama), batch не mark_processed."""
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = _jobs_with_scores()
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    ac_loop.run.side_effect = TimeoutError("ollama timeout")
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, notify_queue)

    with pytest.raises(TimeoutError, match="ollama timeout"):
        uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    notify_queue.enqueue_batch.assert_not_called()
    pending_repo.mark_processed.assert_not_called()


def test_execute_diversifies_selection_by_source_and_tags() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (
            Job(
                id=1,
                title="A",
                description="Desc A",
                raw_html="<p>a</p>",
                source="kwork",
                technologies=["python"],
                final_score=0.91,
                ranker_version="v2",
                reason_codes=["positive_tag_affinity"],
            ),
            0.9,
        ),
        (
            Job(
                id=2,
                title="B",
                description="Desc B",
                raw_html="<p>b</p>",
                source="kwork",
                technologies=["python"],
                final_score=0.89,
                ranker_version="v2",
                reason_codes=["positive_tag_affinity"],
            ),
            0.88,
        ),
        (
            Job(
                id=3,
                title="C",
                description="Desc C",
                raw_html="<p>c</p>",
                source="kwork",
                technologies=["python"],
                final_score=0.87,
                ranker_version="v2",
                reason_codes=["positive_tag_affinity"],
            ),
            0.87,
        ),
    ]
    pending_repo = MagicMock()
    ac_loop = MagicMock()
    ac_loop.run.return_value = ActorCriticResult(
        selection=[
            RankedJob(job_id=1, title="A", why_it_fits="fit A", rank=1, actor_confidence=0.95),
            RankedJob(job_id=2, title="B", why_it_fits="fit B", rank=2, actor_confidence=0.90),
            RankedJob(job_id=3, title="C", why_it_fits="fit C", rank=3, actor_confidence=0.85),
        ],
        final_score=7.0,
        attempts=1,
        passed=True,
    )
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, ac_loop, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2, 3]))

    notify_queue.enqueue_batch.assert_called_once()
    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert [item.job_id for item in jobs] == [1, 2]
    assert jobs[0].final_score == pytest.approx(0.91)
    assert jobs[0].ranker_version == "v2"
    assert jobs[0].reason_codes == ("positive_tag_affinity",)
