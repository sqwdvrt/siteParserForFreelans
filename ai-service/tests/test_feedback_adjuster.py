"""Unit tests for feedback_adjuster: apply_signal and adjust_candidates."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_service.port.feedback_repository import FeedbackSignal
from ai_service.port.match_repository import MatchCandidate
from ai_service.util.feedback_adjuster import adjust_candidates, apply_signal


# ── apply_signal ──────────────────────────────────────────────────────────────


def test_apply_signal_no_data_returns_unchanged() -> None:
    """No feedback data → score unchanged."""
    signal = FeedbackSignal(good_ratio=0.0, bad_ratio=0.0, total=0)
    assert apply_signal(0.80, signal) == 0.80


def test_apply_signal_neutral_signal_no_change() -> None:
    """net within ±0.20 dead-zone → score unchanged."""
    # net = 0.5 - 0.4 = 0.1 → inside dead-zone
    signal = FeedbackSignal(good_ratio=0.5, bad_ratio=0.4, total=10)
    assert apply_signal(0.75, signal) == 0.75


def test_apply_signal_good_majority_boosts_score() -> None:
    """net > 0.20 (mostly likes) → small boost, capped at ×1.15."""
    # net = 0.9 - 0.1 = 0.8  →  multiplier = 1 + 0.8*0.15 = 1.12
    signal = FeedbackSignal(good_ratio=0.9, bad_ratio=0.1, total=20)
    result = apply_signal(0.75, signal)
    assert result == pytest.approx(0.75 * 1.12, rel=1e-6)
    assert result <= 0.75 * 1.15  # never exceeds cap


def test_apply_signal_boost_capped_at_1_15() -> None:
    """Boost multiplier never exceeds 1.15 even with perfect positive net."""
    signal = FeedbackSignal(good_ratio=1.0, bad_ratio=0.0, total=10)
    # net=1.0 → multiplier = 1 + 1.0*0.15 = 1.15 (exactly at cap)
    result = apply_signal(0.80, signal)
    assert result == pytest.approx(0.80 * 1.15, rel=1e-6)


def test_apply_signal_bad_majority_reduces_score() -> None:
    """net < -0.20 (mostly dislikes) → score reduced."""
    # net = 0.1 - 0.9 = -0.8  →  multiplier = 1 + (-0.8)*0.5 = 0.6
    signal = FeedbackSignal(good_ratio=0.1, bad_ratio=0.9, total=20)
    result = apply_signal(0.80, signal)
    assert result == pytest.approx(0.80 * 0.60, rel=1e-6)


def test_apply_signal_penalty_capped_at_0_50() -> None:
    """Penalty multiplier never goes below 0.50."""
    # net = 0.0 - 1.0 = -1.0  →  multiplier = 1 + (-1)*0.5 = 0.5 (exactly at floor)
    signal = FeedbackSignal(good_ratio=0.0, bad_ratio=1.0, total=10)
    result = apply_signal(0.90, signal)
    assert result == pytest.approx(0.90 * 0.50, rel=1e-6)


def test_feedback_signal_net_property() -> None:
    signal = FeedbackSignal(good_ratio=0.7, bad_ratio=0.2, total=10)
    assert signal.net == pytest.approx(0.5)


# ── adjust_candidates ─────────────────────────────────────────────────────────


def _make_feedback_repo(good: float, bad: float, total: int = 10):
    """Return a mock FeedbackRepository that always returns the given signal."""
    repo = MagicMock()
    repo.get_feedback_signal.return_value = FeedbackSignal(
        good_ratio=good, bad_ratio=bad, total=total
    )
    return repo


def test_adjust_candidates_empty_list_returns_empty() -> None:
    repo = _make_feedback_repo(0.0, 0.0, 0)
    result = adjust_candidates([], get_job_skills=lambda j: [], feedback_repo=repo, threshold=0.7)
    assert result == []
    repo.get_feedback_signal.assert_not_called()


def test_adjust_candidates_no_feedback_passes_through() -> None:
    """total=0 → no adjustment, all candidates pass through unchanged."""
    candidates = [
        MatchCandidate(user_id=1, job_id=10, match_score=0.75),
        MatchCandidate(user_id=2, job_id=10, match_score=0.80),
    ]
    repo = _make_feedback_repo(0.0, 0.0, 0)
    result = adjust_candidates(candidates, lambda j: [], repo, threshold=0.70)
    assert len(result) == 2
    assert result[0].match_score == 0.75
    assert result[1].match_score == 0.80


def test_adjust_candidates_good_feedback_boosts_score() -> None:
    """Majority of good feedback → score is boosted."""
    candidates = [MatchCandidate(user_id=1, job_id=10, match_score=0.75)]
    # net = 0.9 - 0.1 = 0.8  →  multiplier = 1.12
    repo = _make_feedback_repo(good=0.9, bad=0.1, total=15)
    result = adjust_candidates(candidates, lambda j: [], repo, threshold=0.70)
    assert len(result) == 1
    assert result[0].match_score == pytest.approx(0.75 * 1.12, rel=1e-6)


def test_adjust_candidates_bad_feedback_reduces_score() -> None:
    """Majority of bad feedback → score reduced; drops below threshold → filtered."""
    # score 0.72, net=-0.8 → multiplier=0.6 → adjusted=0.432 < threshold 0.70
    candidates = [MatchCandidate(user_id=1, job_id=10, match_score=0.72)]
    repo = _make_feedback_repo(good=0.1, bad=0.9, total=15)
    result = adjust_candidates(candidates, lambda j: [], repo, threshold=0.70)
    assert result == []  # filtered out


def test_adjust_candidates_bad_feedback_kept_when_above_threshold() -> None:
    """Score reduced by bad feedback but still above threshold → kept."""
    # score 0.95, net=-0.4 → multiplier = 1 + (-0.4)*0.5 = 0.80 → 0.95*0.80 = 0.76 > 0.70
    candidates = [MatchCandidate(user_id=1, job_id=10, match_score=0.95)]
    repo = _make_feedback_repo(good=0.3, bad=0.7, total=10)
    result = adjust_candidates(candidates, lambda j: [], repo, threshold=0.70)
    assert len(result) == 1
    assert result[0].match_score == pytest.approx(0.95 * 0.80, rel=1e-6)


def test_adjust_candidates_get_job_skills_called_with_correct_job_id() -> None:
    """adjust_candidates calls get_job_skills with each candidate's job_id."""
    candidates = [
        MatchCandidate(user_id=1, job_id=42, match_score=0.80),
        MatchCandidate(user_id=2, job_id=99, match_score=0.75),
    ]
    repo = _make_feedback_repo(0.0, 0.0, 0)
    skills_map: dict[int, list[str]] = {42: ["Python", "Django"], 99: ["Go"]}
    collected_ids: list[int] = []

    def get_skills(job_id: int) -> list[str]:
        collected_ids.append(job_id)
        return skills_map.get(job_id, [])

    adjust_candidates(candidates, get_skills, repo, threshold=0.70)
    assert collected_ids == [42, 99]
    # get_feedback_signal called with skills for each user
    assert repo.get_feedback_signal.call_count == 2
    repo.get_feedback_signal.assert_any_call(1, ["Python", "Django"])
    repo.get_feedback_signal.assert_any_call(2, ["Go"])


def test_adjust_candidates_error_in_feedback_repo_keeps_original() -> None:
    """Exception in feedback_repo → candidate kept with original score."""
    candidates = [MatchCandidate(user_id=1, job_id=10, match_score=0.80)]
    repo = MagicMock()
    repo.get_feedback_signal.side_effect = RuntimeError("db error")
    result = adjust_candidates(candidates, lambda j: [], repo, threshold=0.70)
    assert len(result) == 1
    assert result[0].match_score == 0.80  # original score preserved
