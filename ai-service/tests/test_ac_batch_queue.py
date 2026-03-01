"""Tests for RedisACBatchQueueConsumer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ai_service.adapter.redis.ac_batch_queue import ACBatchMessage, RedisACBatchQueueConsumer


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


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_ack_removes_inflight_payload(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"user_id": 10, "job_ids": [1, 2]})
    mock_client.brpoplpush.return_value = raw
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2])

    queue.ack(10)

    mock_client.lrem.assert_called_once_with("ac-batch:processing", 1, raw)


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_requeues_payload_with_retry_count(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"user_id": 10, "job_ids": [1, 2]})
    mock_client.brpoplpush.return_value = raw
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2])

    queue.nack(10)

    pipe.lrem.assert_called_once_with("ac-batch:processing", 1, raw)
    pipe.rpush.assert_called_once_with("ac-batch", '{"user_id":10,"job_ids":[1,2],"_retry_count":1}')
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_preserves_trace_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"user_id": 10, "job_ids": [1, 2], "trace_id": "trace-abc"})
    mock_client.brpoplpush.return_value = raw
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2], trace_id="trace-abc")

    queue.nack(10)

    pipe.rpush.assert_called_once_with(
        "ac-batch",
        '{"user_id":10,"job_ids":[1,2],"trace_id":"trace-abc","_retry_count":1}',
    )


@patch("ai_service.adapter.redis.ac_batch_queue.redis.from_url")
def test_nack_sends_to_dlq_after_retry_limit(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"user_id": 10, "job_ids": [1, 2], "_retry_count": 5})
    mock_client.brpoplpush.return_value = raw
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    queue = RedisACBatchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == ACBatchMessage(user_id=10, job_ids=[1, 2])

    queue.nack(10)

    pipe.rpush.assert_called_once_with("ac-batch:dlq", '{"user_id":10,"job_ids":[1,2],"_retry_count":6}')
