"""Redis adapter for Actor-Critic batch queue."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import redis

logger = logging.getLogger(__name__)

DEFAULT_QUEUE = "ac-batch"
DEFAULT_MAX_NACK_RETRIES = 5
_RECLAIM_THRESHOLD_ENV = "AI_RECLAIM_STUCK_SEC"
_DEFAULT_RECLAIM_THRESHOLD_SEC = 300.0


def _reclaim_threshold_sec() -> float:
    raw = os.getenv(_RECLAIM_THRESHOLD_ENV, str(_DEFAULT_RECLAIM_THRESHOLD_SEC))
    try:
        v = float(raw)
        return v if v > 0 else _DEFAULT_RECLAIM_THRESHOLD_SEC
    except ValueError:
        return _DEFAULT_RECLAIM_THRESHOLD_SEC


@dataclass(frozen=True)
class ACBatchMessage:
    """Queue payload for one user's Actor-Critic batch."""

    user_id: int
    job_ids: list[int]
    trace_id: str = ""
    traceparent: str = ""


class RedisACBatchQueueConsumer:
    """Consumer/publisher for ac-batch queue."""

    def __init__(self, redis_url: str, queue_name: str = DEFAULT_QUEUE) -> None:
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._queue = queue_name
        self._processing_queue = f"{queue_name}:processing"
        self._dlq_queue = f"{queue_name}:dlq"
        self._max_nack_retries = DEFAULT_MAX_NACK_RETRIES
        self._inflight_by_user_id: dict[int, deque[tuple[str, str]]] = defaultdict(deque)
        self._inflight_lock = threading.Lock()

    def enqueue(self, message: ACBatchMessage) -> None:
        self._client.lpush(self._queue, self._serialize(message))

    def pop_blocking(self, timeout_sec: int = 5) -> ACBatchMessage | None:
        payload = self._client.brpoplpush(self._queue, self._processing_queue, timeout=timeout_sec)
        if payload is None:
            return None
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError as exc:
            logger.warning("invalid ac-batch payload JSON: %s", exc)
            self._ack_raw(payload)
            return None

        parsed = self._parse_payload(data)
        if parsed is None:
            self._ack_raw(payload)
            return None
        # Stamp claim time so reclaim_stuck() can distinguish active vs. stuck items.
        data["_claimed_at"] = time.time()
        stamped = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        pipe = self._client.pipeline(transaction=True)
        pipe.lrem(self._processing_queue, 1, payload)
        pipe.lpush(self._processing_queue, stamped)
        pipe.execute()
        with self._inflight_lock:
            self._inflight_by_user_id[parsed.user_id].append((stamped, parsed.trace_id, parsed.traceparent))
        return parsed

    def ack(self, user_id: int) -> None:
        inflight = self._peek_inflight(user_id)
        if inflight is None:
            return
        raw, _trace_id, _traceparent = inflight
        self._ack_raw(raw)
        self._drop_inflight(user_id, raw)

    def trace_id(self, user_id: int) -> str:
        with self._inflight_lock:
            inflight = self._inflight_by_user_id.get(user_id)
            if not inflight:
                return ""
            _raw, trace_id, _traceparent = inflight[0]
            return trace_id

    def traceparent(self, user_id: int) -> str:
        with self._inflight_lock:
            inflight = self._inflight_by_user_id.get(user_id)
            if not inflight:
                return ""
            _raw, _trace_id, traceparent = inflight[0]
            return traceparent

    def nack(self, user_id: int) -> None:
        inflight = self._peek_inflight(user_id)
        if inflight is None:
            return
        raw, _trace_id, _traceparent = inflight
        out_raw, to_dlq = self._prepare_nack_payload(raw)
        target_queue = self._dlq_queue if to_dlq else self._queue
        pipe = self._client.pipeline(transaction=True)
        pipe.lrem(self._processing_queue, 1, raw)
        pipe.rpush(target_queue, out_raw)
        pipe.execute()
        self._drop_inflight(user_id, raw)

    def reclaim_stuck(self) -> None:
        """Reclaim only items whose lease has expired.

        Items with a fresh ``_claimed_at`` timestamp (within threshold) are
        skipped so that a replica restart does not steal jobs actively processed
        by another replica.  Items without ``_claimed_at`` (legacy or corrupt)
        are always reclaimed.
        """
        threshold = _reclaim_threshold_sec()
        now = time.time()
        items = self._client.lrange(self._processing_queue, 0, -1)
        for raw in items:
            try:
                data = json.loads(raw)
                claimed_at = float(data.get("_claimed_at", 0))
                if claimed_at > 0 and (now - claimed_at) < threshold:
                    continue  # lease still valid — another replica is processing this
            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # malformed: treat as stuck
            pipe = self._client.pipeline(transaction=True)
            pipe.lrem(self._processing_queue, 1, raw)
            pipe.rpush(self._queue, raw)
            pipe.execute()

    def nack_all_inflight(self) -> int:
        with self._inflight_lock:
            inflight: list[tuple[int, str]] = [
                (user_id, raw)
                for user_id, values in self._inflight_by_user_id.items()
                for raw, _trace_id, _tp in values
            ]

        requeued = 0
        for user_id, raw in inflight:
            out_raw, to_dlq = self._prepare_nack_payload(raw)
            target_queue = self._dlq_queue if to_dlq else self._queue
            pipe = self._client.pipeline(transaction=True)
            pipe.lrem(self._processing_queue, 1, raw)
            pipe.rpush(target_queue, out_raw)
            pipe.execute()
            self._drop_inflight(user_id, raw)
            requeued += 1
        return requeued

    @staticmethod
    def _serialize(message: ACBatchMessage) -> str:
        payload: dict[str, Any] = {
            "user_id": message.user_id,
            "job_ids": message.job_ids,
        }
        normalized_trace = RedisACBatchQueueConsumer._normalize_trace_id(message.trace_id)
        if normalized_trace:
            payload["trace_id"] = normalized_trace
        normalized_tp = RedisACBatchQueueConsumer._normalize_trace_id(message.traceparent)
        if normalized_tp:
            payload["traceparent"] = normalized_tp
        return json.dumps(payload)

    @staticmethod
    def _parse_payload(data: dict[str, Any]) -> ACBatchMessage | None:
        user_id = data.get("user_id")
        job_ids = data.get("job_ids")
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        if uid <= 0:
            return None
        if not isinstance(job_ids, list) or not job_ids:
            return None
        parsed_job_ids: list[int] = []
        for raw_job_id in job_ids:
            try:
                jid = int(raw_job_id)
            except (TypeError, ValueError):
                return None
            if jid <= 0:
                return None
            parsed_job_ids.append(jid)
        return ACBatchMessage(
            user_id=uid,
            job_ids=parsed_job_ids,
            trace_id=RedisACBatchQueueConsumer._normalize_trace_id(data.get("trace_id")),
            traceparent=RedisACBatchQueueConsumer._normalize_trace_id(data.get("traceparent")),
        )

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

    def _peek_inflight(self, user_id: int) -> tuple[str, str, str] | None:
        with self._inflight_lock:
            inflight = self._inflight_by_user_id.get(user_id)
            if not inflight:
                return None
            return inflight[0]

    def _drop_inflight(self, user_id: int, raw: str) -> None:
        with self._inflight_lock:
            inflight = self._inflight_by_user_id.get(user_id)
            if not inflight:
                return
            if inflight and inflight[0][0] == raw:
                inflight.popleft()
            else:
                for idx, (candidate_raw, _trace_id, _traceparent) in enumerate(inflight):
                    if candidate_raw == raw:
                        del inflight[idx]
                        break
            if not inflight:
                self._inflight_by_user_id.pop(user_id, None)

    @staticmethod
    def _normalize_trace_id(raw: object) -> str:
        if not isinstance(raw, str):
            return ""
        trace_id = raw.strip()
        if not trace_id:
            return ""
        return trace_id[:128]
