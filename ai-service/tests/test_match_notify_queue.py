"""Tests for RedisMatchNotifyQueue serialization."""

from __future__ import annotations

import json

from ai_service.adapter.redis.match_notify_queue import RedisMatchNotifyQueue
from ai_service.domain.ranked_job import RankedJob
from ai_service.port.match_repository import MatchCandidate
from ai_service.util.trace_context import reset_trace_id, set_trace_id


def test_serialize_includes_candidate_trace_id() -> None:
    payload = RedisMatchNotifyQueue._serialize(
        MatchCandidate(user_id=1, job_id=2, match_score=0.91, trace_id="trace-1")
    )
    data = json.loads(payload)
    assert data["trace_id"] == "trace-1"


def test_serialize_uses_context_trace_id_when_candidate_missing() -> None:
    token = set_trace_id("trace-context-1")
    try:
        payload = RedisMatchNotifyQueue._serialize(
            MatchCandidate(user_id=1, job_id=2, match_score=0.91)
        )
    finally:
        reset_trace_id(token)
    data = json.loads(payload)
    assert data["trace_id"] == "trace-context-1"


def test_serialize_batch_includes_ranked_jobs_and_score() -> None:
    payload = RedisMatchNotifyQueue._serialize_batch(
        user_id=10,
        source="ac",
        ranked_jobs=[
            RankedJob(
                job_id=1,
                title="Backend Python",
                why_it_fits="Good Django fit",
                rank=1,
                actor_confidence=0.9,
            )
        ],
        batch_score=8.24,
    )
    data = json.loads(payload)
    assert data["user_id"] == 10
    assert data["source"] == "ac"
    assert data["batch_score"] == 8.24
    assert "critic_score" not in data
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["job_id"] == 1
    assert data["jobs"][0]["rank"] == 1


def test_serialize_batch_uses_explicit_trace_id() -> None:
    token = set_trace_id("trace-context-should-not-win")
    try:
        payload = RedisMatchNotifyQueue._serialize_batch(
            user_id=10,
            source="rematch",
            ranked_jobs=[
                RankedJob(
                    job_id=1,
                    title="Backend Python",
                    why_it_fits="Good Django fit",
                    rank=1,
                    actor_confidence=0.9,
                )
            ],
            batch_score=8.24,
            trace_id="trace-explicit-1",
        )
    finally:
        reset_trace_id(token)
    data = json.loads(payload)
    assert data["trace_id"] == "trace-explicit-1"


def test_serialize_batch_accepts_legacy_critic_score_alias() -> None:
    payload = RedisMatchNotifyQueue._serialize_batch(
        user_id=10,
        source="ac",
        ranked_jobs=[
            RankedJob(
                job_id=1,
                title="Backend Python",
                why_it_fits="Good Django fit",
                rank=1,
                actor_confidence=0.9,
            )
        ],
        critic_score=8.24,
    )
    data = json.loads(payload)
    assert data["batch_score"] == 8.24
    assert data["critic_score"] == 8.24
