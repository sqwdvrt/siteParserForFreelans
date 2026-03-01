"""Redis adapter: BRPOPLPUSH из user-embed в processing, ack/nack по payload {"user_id": N}."""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from typing import Any

import redis

from ai_service.port.user_embed_queue import UserEmbedQueueConsumer

logger = logging.getLogger(__name__)

DEFAULT_QUEUE = "user-embed"
DEFAULT_MAX_NACK_RETRIES = 5


class RedisUserEmbedQueueConsumer(UserEmbedQueueConsumer):
    """Consumer очереди user-embed через Redis BRPOP."""

    def __init__(self, redis_url: str, queue_name: str = DEFAULT_QUEUE) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._queue = queue_name
        self._processing_queue = f"{queue_name}:processing"
        self._dlq_queue = f"{queue_name}:dlq"
        self._max_nack_retries = DEFAULT_MAX_NACK_RETRIES
        self._inflight_by_user_id: dict[int, deque[tuple[str, str]]] = defaultdict(deque)
        self._inflight_lock = threading.Lock()

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
        trace_id = self._normalize_trace_id(data.get("trace_id"))
        with self._inflight_lock:
            self._inflight_by_user_id[uid].append((payload, trace_id))
        return uid

    def ack(self, user_id: int) -> None:
        inflight = self._take_inflight(user_id)
        if inflight is None:
            return
        raw, _trace_id = inflight
        self._ack_raw(raw)

    def trace_id(self, user_id: int) -> str:
        with self._inflight_lock:
            inflight = self._inflight_by_user_id.get(user_id)
            if not inflight:
                return ""
            _raw, trace_id = inflight[0]
            return trace_id

    def nack(self, user_id: int) -> None:
        inflight = self._take_inflight(user_id)
        if inflight is None:
            return
        raw, _trace_id = inflight
        out_raw, to_dlq = self._prepare_nack_payload(raw)
        target_queue = self._dlq_queue if to_dlq else self._queue
        if to_dlq:
            logger.error("user_id=%s moved to DLQ after retry limit", user_id)
        pipe = self._client.pipeline(transaction=True)
        pipe.lrem(self._processing_queue, 1, raw)
        pipe.rpush(target_queue, out_raw)
        pipe.execute()

    def reclaim_stuck(self) -> None:
        while True:
            moved = self._client.rpoplpush(self._processing_queue, self._queue)
            if moved is None:
                break

    def nack_all_inflight(self) -> int:
        with self._inflight_lock:
            raws: list[str] = [raw for values in self._inflight_by_user_id.values() for raw, _trace_id in values]
            self._inflight_by_user_id.clear()

        for raw in raws:
            out_raw, to_dlq = self._prepare_nack_payload(raw)
            target_queue = self._dlq_queue if to_dlq else self._queue
            pipe = self._client.pipeline(transaction=True)
            pipe.lrem(self._processing_queue, 1, raw)
            pipe.rpush(target_queue, out_raw)
            pipe.execute()
        return len(raws)

    def _ack_raw(self, raw: str) -> None:
        self._client.lrem(self._processing_queue, 1, raw)

    def _prepare_nack_payload(self, raw: str) -> tuple[str, bool]:
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            return raw, True
        if not isinstance(data, dict):
            return raw, True
        try:
            retries = int(data.get("_retry_count", 0))
        except (TypeError, ValueError):
            retries = 0
        retries = max(0, retries) + 1
        data["_retry_count"] = retries
        return (
            json.dumps(data, separators=(",", ":"), ensure_ascii=False),
            retries > self._max_nack_retries,
        )

    def _take_inflight(self, user_id: int) -> tuple[str, str] | None:
        with self._inflight_lock:
            inflight = self._inflight_by_user_id.get(user_id)
            if not inflight:
                return None
            raw = inflight.popleft()
            if not inflight:
                self._inflight_by_user_id.pop(user_id, None)
            return raw

    @staticmethod
    def _normalize_trace_id(raw: object) -> str:
        if not isinstance(raw, str):
            return ""
        trace_id = raw.strip()
        if not trace_id:
            return ""
        return trace_id[:128]
