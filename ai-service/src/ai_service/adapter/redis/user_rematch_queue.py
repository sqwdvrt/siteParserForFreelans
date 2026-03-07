"""Redis adapters for user-rematch queue."""

from __future__ import annotations

import json

import redis

from ai_service.adapter.redis.user_embed_queue import RedisUserEmbedQueueConsumer
from ai_service.port.user_rematch_queue import UserRematchQueue
from ai_service.tracing.setup import inject_context
from ai_service.util.trace_context import get_trace_id

DEFAULT_QUEUE = "user-rematch"


class RedisUserRematchQueue(UserRematchQueue):
    """Producer for user-rematch queue via Redis LPUSH."""

    def __init__(self, redis_url: str, queue_name: str = DEFAULT_QUEUE) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._queue = queue_name

    def enqueue(self, user_id: int) -> None:
        if user_id <= 0:
            raise ValueError(f"user_id must be positive, got {user_id}")
        payload: dict[str, str | int] = {"user_id": user_id}
        trace_id = get_trace_id().strip()[:128]
        if trace_id:
            payload["trace_id"] = trace_id
        traceparent = inject_context(None)
        if traceparent:
            payload["traceparent"] = traceparent
        self._client.lpush(self._queue, json.dumps(payload))


class RedisUserRematchQueueConsumer(RedisUserEmbedQueueConsumer):
    """Consumer очереди user-rematch через Redis BRPOP."""

    def __init__(self, redis_url: str, queue_name: str = DEFAULT_QUEUE) -> None:
        super().__init__(redis_url, queue_name=queue_name)
