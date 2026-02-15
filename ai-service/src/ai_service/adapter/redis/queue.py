"""Redis adapter: BRPOP из ai-process, парсинг JSON {"job_id": N}."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from ai_service.port.queue import JobQueueConsumer

logger = logging.getLogger(__name__)

DEFAULT_QUEUE = "ai-process"


class RedisQueueConsumer(JobQueueConsumer):
    """Consumer очереди ai-process через Redis BRPOP."""

    def __init__(self, redis_url: str, queue_name: str = DEFAULT_QUEUE) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._queue = queue_name

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        result = self._client.brpop(self._queue, timeout=timeout_sec)
        if result is None:
            return None
        _, payload = result
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from queue %s: %s", self._queue, e)
            return None
        job_id = data.get("job_id")
        if job_id is None:
            logger.warning("Missing job_id in payload (len=%d)", len(payload))
            return None
        try:
            jid = int(job_id)
        except (TypeError, ValueError):
            logger.warning("Invalid job_id type: %s", type(job_id))
            return None
        if jid <= 0:
            logger.warning("job_id must be positive, got %d", jid)
            return None
        return jid
