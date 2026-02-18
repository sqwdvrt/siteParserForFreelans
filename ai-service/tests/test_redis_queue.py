"""Тесты RedisQueueConsumer (мок Redis)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ai_service.adapter.redis import RedisQueueConsumer


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_returns_job_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"job_id": 42})
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_returns_none_on_timeout(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = None
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_invalid_json_returns_none(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = "not json"
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_missing_job_id_returns_none(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"other": 1})
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_negative_job_id_returns_none(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"job_id": -1})
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_ack_removes_from_processing(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"job_id": 42})
    mock_client.brpoplpush.return_value = raw
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.ack(42)

    mock_client.lrem.assert_called_once_with("ai-process:processing", 1, raw)


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_nack_requeues_payload(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"job_id": 42})
    mock_client.brpoplpush.return_value = raw
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    mock_client.pipeline.assert_called_once_with(transaction=True)
    pipe.lrem.assert_called_once_with("ai-process:processing", 1, raw)
    pipe.rpush.assert_called_once_with("ai-process", '{"job_id":42,"_retry_count":1}')
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_nack_sends_to_dlq_after_retry_limit(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"job_id": 42, "_retry_count": 5})
    mock_client.brpoplpush.return_value = raw
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    pipe.lrem.assert_called_once_with("ai-process:processing", 1, raw)
    pipe.rpush.assert_called_once_with("ai-process:dlq", '{"job_id":42,"_retry_count":6}')


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_reclaim_stuck_default_moves_until_empty(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.rpoplpush.side_effect = ["a", "b", None]
    c = RedisQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    assert mock_client.rpoplpush.call_count == 3


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_reclaim_stuck_moves_more_than_thousand(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.rpoplpush.side_effect = (["a"] * 1205) + [None]
    c = RedisQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    assert mock_client.rpoplpush.call_count == 1206


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_invalid_job_id_type_returns_none(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"job_id": "abc"})
    c = RedisQueueConsumer("redis://localhost:6379/0")

    assert c.pop_blocking(timeout_sec=1) is None
    mock_client.lrem.assert_called_once()


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_ack_and_nack_no_inflight_are_noops(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    c = RedisQueueConsumer("redis://localhost:6379/0")

    c.ack(999)
    c.nack(999)

    mock_client.lrem.assert_not_called()
    mock_client.pipeline.assert_not_called()


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_prepare_nack_payload_handles_invalid_json(mock_from_url: MagicMock) -> None:
    mock_from_url.return_value = MagicMock()
    c = RedisQueueConsumer("redis://localhost:6379/0")

    out_raw, to_dlq = c._prepare_nack_payload("not-json")
    assert out_raw == "not-json"
    assert to_dlq is True


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_prepare_nack_payload_handles_non_dict(mock_from_url: MagicMock) -> None:
    mock_from_url.return_value = MagicMock()
    c = RedisQueueConsumer("redis://localhost:6379/0")

    out_raw, to_dlq = c._prepare_nack_payload(json.dumps([1, 2, 3]))
    assert out_raw == "[1, 2, 3]"
    assert to_dlq is True


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_prepare_nack_payload_handles_invalid_retry_counter(mock_from_url: MagicMock) -> None:
    mock_from_url.return_value = MagicMock()
    c = RedisQueueConsumer("redis://localhost:6379/0")

    out_raw, to_dlq = c._prepare_nack_payload(json.dumps({"job_id": 1, "_retry_count": "bad"}))
    assert out_raw == '{"job_id":1,"_retry_count":1}'
    assert to_dlq is False
