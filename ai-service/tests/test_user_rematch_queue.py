"""Tests for RedisUserRematchQueue adapters."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ai_service.adapter.redis.user_rematch_queue import RedisUserRematchQueue, RedisUserRematchQueueConsumer

FIXED_TIME = 1000.0


def _stamped(base: dict) -> str:
    return json.dumps({**base, "_claimed_at": FIXED_TIME}, separators=(",", ":"), ensure_ascii=False)


def _nacked(base: dict, retries: int = 1) -> str:
    return json.dumps(
        {**base, "_claimed_at": FIXED_TIME, "_retry_count": retries},
        separators=(",", ":"),
        ensure_ascii=False,
    )


@patch("ai_service.adapter.redis.user_rematch_queue.redis.from_url")
def test_enqueue_pushes_to_user_rematch_queue(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client

    queue = RedisUserRematchQueue("redis://localhost:6379/0")
    queue.enqueue(42)

    mock_client.lpush.assert_called_once()
    queue_name, payload = mock_client.lpush.call_args.args
    assert queue_name == "user-rematch"
    assert '"user_id": 42' in payload


@patch("ai_service.adapter.redis.user_rematch_queue.redis.from_url")
def test_enqueue_rejects_non_positive_user_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client

    queue = RedisUserRematchQueue("redis://localhost:6379/0")

    try:
        queue.enqueue(0)
    except ValueError as exc:
        assert "user_id must be positive" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-positive user_id")

    mock_client.lpush.assert_not_called()


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_uses_user_rematch_queue_name(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"user_id": 42})
    mock_client.brpoplpush.return_value = raw
    queue = RedisUserRematchQueueConsumer("redis://localhost:6379/0")

    assert queue.pop_blocking(timeout_sec=1) == 42
    mock_client.brpoplpush.assert_called_once_with("user-rematch", "user-rematch:processing", timeout=1)


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_ack_failure_keeps_message_inflight_for_shutdown_requeue(
    mock_from_url: MagicMock, _mock_time: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 42})
    mock_client.brpoplpush.return_value = original
    pipe_stamp = MagicMock()
    pipe_nack = MagicMock()
    mock_client.pipeline.side_effect = [pipe_stamp, pipe_nack]
    mock_client.lrem.side_effect = RuntimeError("redis down")
    queue = RedisUserRematchQueueConsumer("redis://localhost:6379/0")
    assert queue.pop_blocking(timeout_sec=1) == 42

    try:
        queue.ack(42)
    except RuntimeError:
        pass

    stamped = _stamped({"user_id": 42})
    assert queue.nack_all_inflight() == 1
    pipe_nack.lrem.assert_called_once_with("user-rematch:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("user-rematch", _nacked({"user_id": 42}))
    pipe_nack.execute.assert_called_once()
