"""Redis adapter: LPUSH в match-notify, payload {"user_id": N, "job_id": M, "match_score": 0.85}."""

from __future__ import annotations

import json
import logging

import redis

from ai_service.port.match_notify_queue import MatchNotifyQueue
from ai_service.port.match_repository import MatchCandidate

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

    @staticmethod
    def _serialize(candidate: MatchCandidate) -> str:
        return json.dumps({
            "user_id": candidate.user_id,
            "job_id": candidate.job_id,
            "match_score": round(candidate.match_score, 4),
        })
