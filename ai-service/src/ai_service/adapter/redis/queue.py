"""Redis adapter: BRPOPLPUSH из ai-process в processing, ack/nack по payload {"job_id": N}."""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
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
        self._processing_queue = f"{queue_name}:processing"
        self._inflight_by_job_id: dict[int, deque[str]] = defaultdict(deque)

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
        job_id = data.get("job_id")
        if job_id is None:
            logger.warning("Missing job_id in payload (len=%d)", len(payload))
            self._ack_raw(payload)  # invalid payload: discard
            return None
        try:
            jid = int(job_id)
        except (TypeError, ValueError):
            logger.warning("Invalid job_id type: %s", type(job_id))
            self._ack_raw(payload)  # invalid payload: discard
            return None
        if jid <= 0:
            logger.warning("job_id must be positive, got %d", jid)
            self._ack_raw(payload)  # invalid payload: discard
            return None
        self._inflight_by_job_id[jid].append(payload)
        return jid

    def ack(self, job_id: int) -> None:
        raw = self._take_inflight_raw(job_id)
        if raw is None:
            return
        self._ack_raw(raw)

    def nack(self, job_id: int) -> None:
        raw = self._take_inflight_raw(job_id)
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

    def _take_inflight_raw(self, job_id: int) -> str | None:
        inflight = self._inflight_by_job_id.get(job_id)
        if not inflight:
            return None
        raw = inflight.popleft()
        if not inflight:
            self._inflight_by_job_id.pop(job_id, None)
        return raw
