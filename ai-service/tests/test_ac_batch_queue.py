"""Tests for RedisACBatchQueueConsumer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ai_service.adapter.redis.ac_batch_queue import ACBatchMessage, RedisACBatchQueueConsumer

FIXED_TIME = 1000.0


def _stamped(base: dict) -> str:
    return json.dumps({**base, "_claimed_at": FIXED_TIME}, separators=(",", ":"), ensure_ascii=False)


def _nacked(base: dict, retries: int = 1) -> str:
    return json.dumps(
        {**base, "_claimed_at": FIXED_TIME, "_retry_count": retries},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _make_pipe_side_effect(mock_client: MagicMock, count: int) -> list[MagicMock]:
    pipes = [MagicMock() for _ in range(count)]
    mock_client.pipeline.side_effect = pipes
    return pipes


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_enqueue_serializes_payload(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    queue.enqueue(ACBatchMessage(user_id=10, job_ids=[1, 2, 3]))

    raw = mock_client.lpush.call_args[0][1]
    assert json.loads(raw) == {"user_id": 10, "job_ids": [1, 2, 3]}


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_enqueue_serializes_trace_id_when_present(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    queue.enqueue(ACBatchMessage(user_id=10, job_ids=[1, 2, 3], trace_id="trace-123"))

    raw = mock_client.lpush.call_args[0][1]
    assert json.loads(raw) == {"user_id": 10, "job_ids": [1, 2, 3], "trace_id": "trace-123"}


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_pop_blocking_returns_message(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"user_id": 10, "job_ids": [1, 2]})
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    message = queue.pop_blocking(timeout_sec=1)

    assert message == ACBatchMessage(user_id=10, job_ids=[1, 2])


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_pop_blocking_returns_message_with_trace_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"user_id": 10, "job_ids": [1, 2], "trace_id": "trace-xyz"})
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    message = queue.pop_blocking(timeout_sec=1)

    assert message == ACBatchMessage(user_id=10, job_ids=[1, 2], trace_id="trace-xyz")
    assert queue.trace_id(10) == "trace-xyz"


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_pop_blocking_returns_none_on_timeout(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = None
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    assert queue.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_pop_blocking_invalid_json_returns_none_and_discards_payload(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = "not-json"
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    assert queue.pop_blocking(timeout_sec=1) is None
    mock_client.lrem.assert_called_once_with("ac-batch:processing", 1, "not-json")


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_pop_blocking_invalid_payload_returns_none_and_discards(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"user_id": 10, "job_ids": ["bad"]})
    mock_client.brpoplpush.return_value = raw
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    assert queue.pop_blocking(timeout_sec=1) is None
    mock_client.lrem.assert_called_once_with("ac-batch:processing", 1, raw)


@patch("ai_service.adapter.redis.ac_batch_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_ack_removes_inflight_payload(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 10, "job_ids": [1, 2]})
    mock_client.brpoplpush.return_value = original
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2])

    queue.ack(10)

    stamped = _stamped({"user_id": 10, "job_ids": [1, 2]})
    mock_client.lrem.assert_called_once_with("ac-batch:processing", 1, stamped)


@patch("ai_service.adapter.redis.ac_batch_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_ack_failure_keeps_batch_inflight_for_shutdown_requeue(
    mock_from_url: MagicMock, _mock_time: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 10, "job_ids": [1, 2]})
    mock_client.brpoplpush.return_value = original
    pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    mock_client.lrem.side_effect = RuntimeError("redis down")
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2])

    try:
        queue.ack(10)
    except RuntimeError:
        pass

    stamped = _stamped({"user_id": 10, "job_ids": [1, 2]})
    assert queue.nack_all_inflight() == 1
    pipe_nack.lrem.assert_called_once_with("ac-batch:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("ac-batch", _nacked({"user_id": 10, "job_ids": [1, 2]}))
    pipe_nack.execute.assert_called_once()


@patch("ai_service.adapter.redis.ac_batch_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_requeues_payload_with_retry_count(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 10, "job_ids": [1, 2]})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2])

    queue.nack(10)

    stamped = _stamped({"user_id": 10, "job_ids": [1, 2]})
    pipe_nack.lrem.assert_called_once_with("ac-batch:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("ac-batch", _nacked({"user_id": 10, "job_ids": [1, 2]}))
    pipe_nack.execute.assert_called_once()


@patch("ai_service.adapter.redis.ac_batch_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_preserves_trace_id(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 10, "job_ids": [1, 2], "trace_id": "trace-abc"})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2], trace_id="trace-abc")

    queue.nack(10)

    expected = _nacked({"user_id": 10, "job_ids": [1, 2], "trace_id": "trace-abc"})
    pipe_nack.rpush.assert_called_once_with("ac-batch", expected)


@patch("ai_service.adapter.redis.ac_batch_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_sends_to_dlq_after_retry_limit(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 10, "job_ids": [1, 2], "_retry_count": 5})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2])

    queue.nack(10)

    dlq_payload = json.dumps(
        {"user_id": 10, "job_ids": [1, 2], "_retry_count": 6, "_claimed_at": FIXED_TIME},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    pipe_nack.rpush.assert_called_once_with("ac-batch:dlq", dlq_payload)


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_reclaim_stuck_skips_fresh_items(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    import time
    fresh = json.dumps({"user_id": 1, "job_ids": [1], "_claimed_at": time.time()})
    mock_client.lrange.return_value = [fresh]
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    queue.reclaim_stuck()

    mock_client.pipeline.assert_not_called()


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_reclaim_stuck_reclaims_expired_items(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    expired = json.dumps({"user_id": 2, "job_ids": [2], "_claimed_at": 1.0})
    mock_client.lrange.return_value = [expired]
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    queue.reclaim_stuck()

    pipe.lrem.assert_called_once_with("ac-batch:processing", 1, expired)
    pipe.rpush.assert_called_once_with("ac-batch", expired)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_reclaim_stuck_reclaims_missing_claimed_at(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    legacy = json.dumps({"user_id": 3, "job_ids": [3]})
    mock_client.lrange.return_value = [legacy]
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    queue.reclaim_stuck()

    pipe.lrem.assert_called_once_with("ac-batch:processing", 1, legacy)
    pipe.rpush.assert_called_once_with("ac-batch", legacy)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.ac_batch_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_all_inflight_requeues_all_local_messages(
    mock_from_url: MagicMock, _mock_time: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw_1 = json.dumps({"user_id": 10, "job_ids": [1, 2]})
    raw_2 = json.dumps({"user_id": 20, "job_ids": [3], "trace_id": "trace-b"})
    mock_client.brpoplpush.side_effect = [raw_1, raw_2]
    pipe_s1, pipe_s2, pipe_n1, pipe_n2 = _make_pipe_side_effect(mock_client, 4)
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking() == ACBatchMessage(user_id=10, job_ids=[1, 2])
    assert queue.pop_blocking() == ACBatchMessage(user_id=20, job_ids=[3], trace_id="trace-b")

    assert queue.nack_all_inflight() == 2

    stamped1 = _stamped({"user_id": 10, "job_ids": [1, 2]})
    stamped2 = _stamped({"user_id": 20, "job_ids": [3], "trace_id": "trace-b"})
    pipe_n1.lrem.assert_called_once_with("ac-batch:processing", 1, stamped1)
    pipe_n2.lrem.assert_called_once_with("ac-batch:processing", 1, stamped2)
    pipe_n1.rpush.assert_called_once_with("ac-batch", _nacked({"user_id": 10, "job_ids": [1, 2]}))
    pipe_n2.rpush.assert_called_once_with("ac-batch", _nacked({"user_id": 20, "job_ids": [3], "trace_id": "trace-b"}))


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_all_inflight_returns_zero_when_nothing_inflight(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")

    assert queue.nack_all_inflight() == 0
    mock_client.pipeline.assert_not_called()
