"""Tests for ProcessUserRematchUseCase — including feedback adjustment."""

from __future__ import annotations

from unittest.mock import MagicMock

from ai_service.domain.job import Job
from ai_service.domain.user import User, UserPreferences
from ai_service.port.feedback_repository import FeedbackSignal
from ai_service.port.match_repository import MatchCandidate
from ai_service.port.repository import JobEmbeddingRecord
from ai_service.usecase.process_user_rematch import ProcessUserRematchUseCase


class _StubJobRepo:
    def __init__(self, jobs=None, embeddings=None) -> None:
        self._jobs = jobs or {}
        self._embeddings = embeddings or {}

    def get(self, job_id: int):
        return self._jobs.get(job_id)

    def get_embedding(self, job_id: int):
        return self._embeddings.get(job_id)


def _make_use_case(
    embedding=None,
    candidates=None,
    feedback_repo=None,
    threshold: float = 0.7,
    user=None,
    job_repo=None,
):
    user_repo = MagicMock()
    user_repo.get_embedding.return_value = embedding or [0.1] * 384
    user_repo.get_by_id.return_value = user or User(
        id=1,
        telegram_id=1001,
        profile_text="python backend developer",
        embedding=embedding or [0.1] * 384,
    )

    match_repo = MagicMock()
    match_repo.find_jobs_for_user.return_value = candidates or []

    queue = MagicMock()

    default_job_repo = _StubJobRepo(
        jobs={
            candidate.job_id: Job(
                id=candidate.job_id,
                source="kwork",
                url=f"https://example.com/jobs/{candidate.job_id}",
                title=f"Backend job {candidate.job_id}",
                description="Python backend project",
                raw_html="python backend",
            )
            for candidate in (candidates or [])
        }
    )

    uc = ProcessUserRematchUseCase(
        user_repo,
        match_repo,
        queue,
        job_repo=job_repo or default_job_repo,
        similarity_threshold=threshold,
        feedback_repo=feedback_repo,
    )
    return uc, user_repo, match_repo, queue


def test_execute_skips_when_no_embedding() -> None:
    uc, user_repo, match_repo, queue = _make_use_case(embedding=None)
    user_repo.get_embedding.return_value = None
    assert uc.execute(42) == 0
    match_repo.find_jobs_for_user.assert_not_called()
    queue.enqueue.assert_not_called()


def test_execute_enqueues_all_candidates_without_feedback_repo() -> None:
    candidates = [
        MatchCandidate(user_id=1, job_id=10, match_score=0.80),
        MatchCandidate(user_id=1, job_id=11, match_score=0.75),
    ]
    uc, _, _, queue = _make_use_case(candidates=candidates, feedback_repo=None)
    result = uc.execute(1)
    assert result == 2
    queue.enqueue_many.assert_called_once_with(candidates)
    queue.enqueue.assert_not_called()


def test_execute_applies_feedback_adjustment() -> None:
    """feedback_repo present → adjust_candidates is applied."""
    candidates = [
        MatchCandidate(user_id=1, job_id=10, match_score=0.75),
        MatchCandidate(user_id=1, job_id=11, match_score=0.72),
    ]
    feedback_repo = MagicMock()
    # Good feedback for job 10: boost score
    # Bad feedback for job 11: reduce below threshold → filter
    def side_effect(user_id, job_skills):
        return FeedbackSignal(good_ratio=0.9, bad_ratio=0.1, total=10)

    feedback_repo.get_feedback_signal.side_effect = side_effect

    uc, _, _, queue = _make_use_case(candidates=candidates, feedback_repo=feedback_repo)
    result = uc.execute(1)
    # Both have net=0.8 → boost → both above threshold, both enqueued
    assert result == 2
    queue.enqueue_many.assert_called_once()
    enqueued = queue.enqueue_many.call_args.args[0]
    assert len(enqueued) == 2
    assert all(item.match_score > 0.75 for item in enqueued)


def test_execute_filters_candidate_below_threshold_after_feedback() -> None:
    """Candidate whose score drops below threshold after bad feedback is not enqueued."""
    candidates = [
        MatchCandidate(user_id=1, job_id=10, match_score=0.72),  # will be reduced
    ]
    feedback_repo = MagicMock()
    # net = 0.1 - 0.9 = -0.8  →  multiplier=0.6  →  0.72*0.6=0.432 < 0.70
    feedback_repo.get_feedback_signal.return_value = FeedbackSignal(
        good_ratio=0.1, bad_ratio=0.9, total=10
    )
    uc, _, _, queue = _make_use_case(candidates=candidates, feedback_repo=feedback_repo)
    result = uc.execute(1)
    assert result == 0
    queue.enqueue.assert_not_called()


def test_execute_uses_empty_skills_for_global_signal() -> None:
    """In rematch direction, get_feedback_signal is called with empty skills list."""
    candidates = [MatchCandidate(user_id=1, job_id=10, match_score=0.80)]
    feedback_repo = MagicMock()
    feedback_repo.get_feedback_signal.return_value = FeedbackSignal(
        good_ratio=0.0, bad_ratio=0.0, total=0
    )
    uc, _, _, queue = _make_use_case(candidates=candidates, feedback_repo=feedback_repo)
    uc.execute(1)
    feedback_repo.get_feedback_signal.assert_called_once_with(1, [])


def test_execute_uses_single_enqueue_for_one_candidate() -> None:
    candidates = [MatchCandidate(user_id=1, job_id=10, match_score=0.80)]
    uc, _, _, queue = _make_use_case(candidates=candidates, feedback_repo=None)

    result = uc.execute(1)

    assert result == 1
    queue.enqueue_many.assert_called_once_with(candidates)
    queue.enqueue.assert_not_called()


def test_execute_applies_preference_filter_before_enqueue() -> None:
    candidate = MatchCandidate(user_id=1, job_id=10, match_score=0.82)
    user = User(
        id=1,
        telegram_id=1001,
        profile_text="python backend developer",
        embedding=[0.1] * 384,
        preferences=UserPreferences(exclude_keywords=("laravel",)),
    )
    job_repo = _StubJobRepo(
        jobs={
            10: Job(
                id=10,
                source="kwork",
                url="https://example.com/jobs/10",
                title="Laravel developer needed",
                description="Support existing PHP Laravel project",
                raw_html="Laravel maintenance",
            )
        }
    )
    uc, _, match_repo, queue = _make_use_case(
        candidates=[candidate],
        user=user,
        job_repo=job_repo,
    )

    result = uc.execute(1)

    assert result == 0
    match_repo.find_jobs_for_user.assert_called_once_with([0.1] * 384, 1, 0.7, 50, 7)
    queue.enqueue_many.assert_called_once_with([])
    queue.enqueue.assert_not_called()


def test_execute_keeps_preference_matched_candidate() -> None:
    candidate = MatchCandidate(user_id=1, job_id=10, match_score=0.82)
    user = User(
        id=1,
        telegram_id=1001,
        profile_text="python backend developer",
        embedding=[0.1] * 384,
        preferences=UserPreferences(include_keywords=("python",)),
    )
    job_repo = _StubJobRepo(
        jobs={
            10: Job(
                id=10,
                source="kwork",
                url="https://example.com/jobs/10",
                title="Python backend developer needed",
                description="FastAPI and PostgreSQL project",
                raw_html="python fastapi",
            )
        },
        embeddings={
            10: JobEmbeddingRecord(
                embedding=[0.2] * 384,
                metadata={"classification": {"technologies": ["python", "fastapi"]}},
            )
        },
    )
    uc, _, _, queue = _make_use_case(
        candidates=[candidate],
        user=user,
        job_repo=job_repo,
    )

    result = uc.execute(1)

    assert result == 1
    queue.enqueue_many.assert_called_once_with([candidate])
    queue.enqueue.assert_not_called()
