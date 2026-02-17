"""Redis adapter: BRPOPLPUSH из user-embed в processing, ack/nack по payload {"user_id": N}."""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
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
        self._processing_queue = f"{queue_name}:processing"
        self._inflight_by_user_id: dict[int, deque[str]] = defaultdict(deque)

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        payload = self._client.brpoplpush(self._queue, self._processing_queue, timeout=timeout_sec)
        if payload is None:
            return None
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from queue %s: %s", self._queue, e)
            self._ack_raw(payload)  # poison payload: discard
            return None
        user_id = data.get("user_id")
        if user_id is None:
            logger.warning("Missing user_id in payload (len=%d)", len(payload))
            self._ack_raw(payload)  # invalid payload: discard
            return None
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            logger.warning("Invalid user_id type: %s", type(user_id))
            self._ack_raw(payload)  # invalid payload: discard
            return None
        if uid <= 0:
            logger.warning("user_id must be positive, got %d", uid)
            self._ack_raw(payload)  # invalid payload: discard
            return None
        self._inflight_by_user_id[uid].append(payload)
        return uid

    def ack(self, user_id: int) -> None:
        raw = self._take_inflight_raw(user_id)
        if raw is None:
            return
        self._ack_raw(raw)

    def nack(self, user_id: int) -> None:
        raw = self._take_inflight_raw(user_id)
        if raw is None:
            return
        pipe = self._client.pipeline(transaction=True)
        pipe.lrem(self._processing_queue, 1, raw)
        pipe.lpush(self._queue, raw)
        pipe.execute()

    def reclaim_stuck(self, max_items: int = 1000) -> None:
        for _ in range(max_items):
            moved = self._client.rpoplpush(self._processing_queue, self._queue)
            if moved is None:
                break

    def _ack_raw(self, raw: str) -> None:
        self._client.lrem(self._processing_queue, 1, raw)

    def _take_inflight_raw(self, user_id: int) -> str | None:
        inflight = self._inflight_by_user_id.get(user_id)
        if not inflight:
            return None
        raw = inflight.popleft()
        if not inflight:
            self._inflight_by_user_id.pop(user_id, None)
        return raw
