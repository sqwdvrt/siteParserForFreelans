"""Redis adapter: LPUSH в match-notify, payload {"user_id": N, "job_id": M, "match_score": 0.85}."""

from __future__ import annotations

import json
import logging

import redis

from ai_service.domain.ranked_job import RankedJob
from ai_service.port.match_notify_queue import MatchNotifyQueue
from ai_service.port.match_repository import MatchCandidate
from ai_service.util.trace_context import get_trace_id

logger = logging.getLogger(__name__)

DEFAULT_QUEUE = "match-notify"


class RedisMatchNotifyQueue(MatchNotifyQueue):
    """MatchNotifyQueue через Redis LPUSH."""

    def __init__(self, redis_url: str, queue_name: str = DEFAULT_QUEUE) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._queue = queue_name

    def enqueue(self, candidate: MatchCandidate) -> None:
        payload = self._serialize(candidate)
        self._client.lpush(self._queue, payload)

    def enqueue_many(self, candidates: list[MatchCandidate]) -> None:
        if not candidates:
            return
        pipe = self._client.pipeline(transaction=False)
        for candidate in candidates:
            pipe.lpush(self._queue, self._serialize(candidate))
        pipe.execute()

    def enqueue_batch(
        self,
        *,
        user_id: int,
        ranked_jobs: list[RankedJob],
        critic_score: float,
        trace_id: str = "",
    ) -> None:
        if not ranked_jobs:
            return
        payload = self._serialize_batch(
            user_id=user_id,
            ranked_jobs=ranked_jobs,
            critic_score=critic_score,
            trace_id=trace_id,
        )
        self._client.lpush(self._queue, payload)

    @staticmethod
    def _serialize(candidate: MatchCandidate) -> str:
        trace_id = (candidate.trace_id or get_trace_id()).strip()[:128]
        payload: dict = {
            "user_id": candidate.user_id,
            "job_id": candidate.job_id,
            "match_score": round(candidate.match_score, 4),
        }
        if candidate.why_it_fits:
            payload["why_it_fits"] = candidate.why_it_fits
        if trace_id:
            payload["trace_id"] = trace_id
        return json.dumps(payload)

    @staticmethod
    def _serialize_batch(
        *,
        user_id: int,
        ranked_jobs: list[RankedJob],
        critic_score: float,
        trace_id: str = "",
    ) -> str:
        normalized_trace = (trace_id or get_trace_id()).strip()[:128]
        payload: dict = {
            "user_id": user_id,
            "critic_score": round(float(critic_score), 2),
            "jobs": [
                {
                    "job_id": item.job_id,
                    "title": item.title,
                    "why_it_fits": item.why_it_fits,
                    "rank": item.rank,
                }
                for item in ranked_jobs
            ],
        }
        if normalized_trace:
            payload["trace_id"] = normalized_trace
        return json.dumps(payload)
