"""Tests for ProcessACBatchUseCase."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from ai_service.domain.job import Job
from ai_service.domain.user import User, UserPreferences
from ai_service.usecase.process_ac_batch import ACBatch, ProcessACBatchUseCase


def _user_with_profile(*, preferred_sources: tuple[str, ...] = ()) -> User:
    return User(
        id=1,
        telegram_id=123,
        profile_text="Python backend",
        embedding=[0.1] * 384,
        preferences=UserPreferences(preferred_sources=preferred_sources),
    )


def _jobs_with_scores() -> list[tuple[Job, float]]:
    return [
        (Job(id=1, title="A", description="Desc A", raw_html="<p>a</p>", rerank_score=0.9), 0.9),
        (Job(id=2, title="B", description="Desc B", raw_html="<p>b</p>", rerank_score=0.8), 0.8),
    ]


def test_execute_skips_when_user_has_no_profile() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(id=1, telegram_id=123, profile_text=None, embedding=[0.1] * 384)
    job_repo = MagicMock()
    pending_repo = MagicMock()
    actor = MagicMock()
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[10, 20]))

    pending_repo.mark_processed.assert_called_once_with(1, [10, 20])
    actor.explain_batch.assert_not_called()
    notify_queue.enqueue_batch.assert_not_called()


def test_execute_skips_when_user_has_no_embedding() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(id=1, telegram_id=123, profile_text="Has profile", embedding=None)
    job_repo = MagicMock()
    pending_repo = MagicMock()
    actor = MagicMock()
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[10]))

    pending_repo.release_claim.assert_called_once_with(1, [10])
    pending_repo.mark_processed.assert_not_called()
    actor.explain_batch.assert_not_called()


def test_execute_marks_processed_when_no_jobs_loaded() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = []
    pending_repo = MagicMock()
    actor = MagicMock()
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])
    actor.explain_batch.assert_not_called()


def test_execute_enqueues_top_scored_batch() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = _jobs_with_scores()
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["Strong Python fit.", "Solid backend fit."]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    actor.explain_batch.assert_called_once()
    notify_queue.enqueue_batch.assert_called_once()
    kwargs = notify_queue.enqueue_batch.call_args.kwargs
    assert kwargs["user_id"] == 1
    assert kwargs["batch_score"] == pytest.approx(8.5)
    assert [item.job_id for item in kwargs["ranked_jobs"]] == [1, 2]
    assert kwargs["ranked_jobs"][0].why_it_fits == "Strong Python fit."
    assert kwargs["ranked_jobs"][0].final_score == pytest.approx(0.9)
    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])


def test_execute_filters_out_jobs_below_rerank_threshold() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (Job(id=1, title="A", description="Desc A", raw_html="<p>a</p>", rerank_score=0.91), 0.9),
        (Job(id=2, title="B", description="Desc B", raw_html="<p>b</p>", rerank_score=0.40), 0.8),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["Strong Python fit."]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(
        user_repo,
        job_repo,
        pending_repo,
        actor,
        notify_queue,
        rerank_threshold=0.55,
    )

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    notify_queue.enqueue_batch.assert_called_once()
    kwargs = notify_queue.enqueue_batch.call_args.kwargs
    assert [item.job_id for item in kwargs["ranked_jobs"]] == [1]
    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])


def test_execute_skips_notification_when_queue_has_no_batch_method() -> None:
    class _QueueWithoutBatch:
        pass

    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = _jobs_with_scores()
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["Strong Python fit.", "Solid backend fit."]
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, _QueueWithoutBatch())

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    pending_repo.mark_processed.assert_called_once_with(1, [1, 2])


def test_execute_propagates_actor_timeout_and_keeps_batch_unprocessed() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = _jobs_with_scores()
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.side_effect = TimeoutError("actor timeout")
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    with pytest.raises(TimeoutError, match="actor timeout"):
        uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    notify_queue.enqueue_batch.assert_not_called()
    pending_repo.mark_processed.assert_not_called()


def test_execute_uses_feedback_signal_in_final_score() -> None:
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
                technologies=["python"],
                rerank_score=0.70,
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
                technologies=["go"],
                rerank_score=0.69,
                ranker_version="v2",
                reason_codes=["positive_tag_affinity"],
            ),
            0.7,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["fit A", "fit B"]
    feedback_repo = MagicMock()
    feedback_repo.get_feedback_signal.side_effect = [
        MagicMock(net=0.5),
        MagicMock(net=-0.5),
    ]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(
        user_repo,
        job_repo,
        pending_repo,
        actor,
        notify_queue,
        feedback_repo=feedback_repo,
    )

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    notify_queue.enqueue_batch.assert_called_once()
    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert [item.job_id for item in jobs] == [1, 2]
    assert jobs[0].final_score == pytest.approx(0.805)
    assert jobs[1].final_score == pytest.approx(0.5865)
    assert jobs[0].ranker_version == "v2"
    assert jobs[0].reason_codes == ("positive_tag_affinity",)
    assert feedback_repo.get_feedback_signal.call_count == 2


def test_execute_cold_start_without_feedback_keeps_rerank_score() -> None:
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
                technologies=["python"],
                rerank_score=0.73,
            ),
            0.95,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["fit A"]
    feedback_repo = MagicMock()
    feedback_repo.get_feedback_signal.return_value = MagicMock(net=0.0)
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(
        user_repo,
        job_repo,
        pending_repo,
        actor,
        notify_queue,
        feedback_repo=feedback_repo,
    )

    uc.execute(ACBatch(user_id=1, job_ids=[1]))

    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert jobs[0].final_score == pytest.approx(0.73)


def test_execute_caps_feedback_bonus_when_all_feedback_positive() -> None:
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
                technologies=["python"],
                rerank_score=0.70,
            ),
            0.9,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["fit A"]
    feedback_repo = MagicMock()
    feedback_repo.get_feedback_signal.return_value = MagicMock(net=1.0)
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(
        user_repo,
        job_repo,
        pending_repo,
        actor,
        notify_queue,
        feedback_repo=feedback_repo,
    )

    uc.execute(ACBatch(user_id=1, job_ids=[1]))

    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert jobs[0].final_score == pytest.approx(0.91)


def test_execute_uses_neutral_multiplier_when_preferred_sources_empty() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile(preferred_sources=())
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (
            Job(
                id=1,
                title="A",
                description="Desc A",
                raw_html="<p>a</p>",
                source="upwork",
                technologies=["python"],
                rerank_score=0.70,
            ),
            0.9,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["fit A"]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1]))

    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert jobs[0].final_score == pytest.approx(0.70)


def test_execute_applies_preference_multiplier_when_list_is_not_empty() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile(preferred_sources=("upwork",))
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (
            Job(
                id=1,
                title="Preferred",
                description="Desc A",
                raw_html="<p>a</p>",
                source="upwork",
                technologies=["python"],
                rerank_score=0.70,
            ),
            0.9,
        ),
        (
            Job(
                id=2,
                title="Non-preferred",
                description="Desc B",
                raw_html="<p>b</p>",
                source="kwork",
                technologies=["python"],
                rerank_score=0.70,
            ),
            0.9,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["fit preferred", "fit non-preferred"]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert [item.job_id for item in jobs] == [1, 2]
    assert jobs[0].final_score == pytest.approx(0.84)
    assert jobs[1].final_score == pytest.approx(0.56)


def test_execute_persists_scoring_components_to_pending_repo() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile(preferred_sources=("upwork",))
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (
            Job(
                id=1,
                title="A",
                description="Desc A",
                raw_html="<p>a</p>",
                source="upwork",
                technologies=["python"],
                rerank_score=0.70,
            ),
            0.9,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["fit A"]
    feedback_repo = MagicMock()
    feedback_repo.get_feedback_signal.return_value = MagicMock(net=1.0)
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(
        user_repo,
        job_repo,
        pending_repo,
        actor,
        notify_queue,
        feedback_repo=feedback_repo,
    )

    uc.execute(ACBatch(user_id=1, job_ids=[1]))

    pending_repo.save_scoring_components.assert_called_once()
    args = pending_repo.save_scoring_components.call_args.args
    assert args[0] == 1
    row = args[1][0]
    assert row[0] == 1
    assert row[1] == pytest.approx(0.70)
    assert row[2] == pytest.approx(0.3)
    assert row[3] == pytest.approx(1.2)
    assert row[4] == pytest.approx(1.092)


def test_execute_falls_back_when_actor_returns_invalid_length() -> None:
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
                technologies=["python"],
                rerank_score=0.80,
            ),
            0.8,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = []
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1]))

    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert jobs[0].why_it_fits == "Подходит по стеку: python."


def test_execute_applies_time_decay_multiplier() -> None:
    now = datetime.now(timezone.utc)
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (
            Job(
                id=1,
                title="Fresh",
                description="Desc",
                raw_html="<p>a</p>",
                source="freelancehunt",
                rerank_score=0.7,
                posted_at=now - timedelta(hours=1),
            ),
            0.9,
        ),
        (
            Job(
                id=2,
                title="Old",
                description="Desc",
                raw_html="<p>b</p>",
                source="freelancehunt",
                rerank_score=0.7,
                posted_at=now - timedelta(hours=72),
            ),
            0.9,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["fresh", "old"]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert [item.job_id for item in jobs] == [1, 2]
    assert jobs[0].final_score == pytest.approx(0.7 * math.exp(-1 / 48), rel=1e-2)
    assert jobs[1].final_score == pytest.approx(0.7 * math.exp(-72 / 48), rel=1e-2)


def test_execute_applies_competition_penalty_from_kwork_html() -> None:
    now = datetime.now(timezone.utc)
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (
            Job(
                id=1,
                title="Crowded",
                description="Desc",
                raw_html="<div>Откликов: 50</div>",
                source="kwork",
                rerank_score=0.7,
                posted_at=now,
            ),
            0.9,
        ),
        (
            Job(
                id=2,
                title="Calm",
                description="Desc",
                raw_html="<div>Новый проект</div>",
                source="kwork",
                rerank_score=0.7,
                posted_at=now,
            ),
            0.9,
        ),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["crowded", "calm"]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(user_repo, job_repo, pending_repo, actor, notify_queue)

    uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    jobs = notify_queue.enqueue_batch.call_args.kwargs["ranked_jobs"]
    assert [item.job_id for item in jobs] == [2, 1]
    assert jobs[0].final_score == pytest.approx(0.7, rel=1e-2)
    assert jobs[1].final_score == pytest.approx(0.35, rel=1e-2)
