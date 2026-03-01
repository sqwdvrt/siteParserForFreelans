"""Tests for AccumulateMatchesUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_service.port.match_repository import MatchCandidate
from ai_service.usecase.accumulate_matches import AccumulateMatchesUseCase


def test_execute_upserts_all_matches() -> None:
    pending_repo = MagicMock()
    uc = AccumulateMatchesUseCase(pending_repo)
    matches = [
        MatchCandidate(user_id=10, job_id=1, match_score=0.85),
        MatchCandidate(user_id=20, job_id=1, match_score=0.72),
    ]

    uc.execute(matches)

    pending_repo.upsert_many.assert_called_once_with(
        [
            (10, 1, 0.85, ""),
            (20, 1, 0.72, ""),
        ]
    )


def test_execute_raises_and_stops_when_upsert_fails() -> None:
    pending_repo = MagicMock()
    pending_repo.upsert_many.side_effect = RuntimeError("boom")
    uc = AccumulateMatchesUseCase(pending_repo)
    matches = [
        MatchCandidate(user_id=10, job_id=1, match_score=0.85),
        MatchCandidate(user_id=20, job_id=1, match_score=0.72),
    ]

    with pytest.raises(RuntimeError, match="boom"):
        uc.execute(matches)

    pending_repo.upsert_many.assert_called_once()


def test_execute_passes_trace_id_to_repository() -> None:
    pending_repo = MagicMock()
    uc = AccumulateMatchesUseCase(pending_repo)
    matches = [
        MatchCandidate(user_id=10, job_id=1, match_score=0.85, trace_id="trace-ac-1"),
    ]

    uc.execute(matches)

    pending_repo.upsert_many.assert_called_once_with(
        [
            (10, 1, 0.85, "trace-ac-1"),
        ]
    )
