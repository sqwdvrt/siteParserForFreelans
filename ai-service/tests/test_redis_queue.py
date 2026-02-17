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
    pipe.lpush.assert_called_once_with("ai-process", raw)
    pipe.execute.assert_called_once()
