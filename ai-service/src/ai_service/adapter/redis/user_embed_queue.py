"""Redis adapter: BRPOP из user-embed, парсинг JSON {"user_id": N}."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from ai_service.port.user_embed_queue import UserEmbedQueueConsumer

logger = logging.getLogger(__name__)

DEFAULT_QUEUE = "user-embed"


class RedisUserEmbedQueueConsumer(UserEmbedQueueConsumer):
    """Consumer очереди user-embed через Redis BRPOP."""

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
        user_id = data.get("user_id")
        if user_id is None:
            logger.warning("Missing user_id in payload (len=%d)", len(payload))
            return None
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            logger.warning("Invalid user_id type: %s", type(user_id))
            return None
        if uid <= 0:
            logger.warning("user_id must be positive, got %d", uid)
            return None
        return uid
