"""Тесты RedisUserEmbedQueueConsumer (мок Redis)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

from ai_service.adapter.redis.user_embed_queue import RedisUserEmbedQueueConsumer

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


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_returns_user_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"user_id": 42})
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_exposes_trace_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"user_id": 42, "trace_id": "trace-xyz"})
    mock_client.brpoplpush.return_value = raw
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42
    assert c.trace_id(42) == "trace-xyz"


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_returns_none_on_timeout(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = None
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_invalid_json_returns_none(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = "not json"
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_missing_user_id_returns_none(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"job_id": 1})
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_negative_user_id_returns_none(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"user_id": -1})
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) is None


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_pop_blocking_injects_claimed_at(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 42})
    mock_client.brpoplpush.return_value = original
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")

    assert c.pop_blocking(timeout_sec=1) == 42

    stamped = _stamped({"user_id": 42})
    pipe.lrem.assert_called_once_with("user-embed:processing", 1, original)
    pipe.lpush.assert_called_once_with("user-embed:processing", stamped)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_ack_removes_from_processing(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 42})
    mock_client.brpoplpush.return_value = original
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.ack(42)

    stamped = _stamped({"user_id": 42})
    mock_client.lrem.assert_called_once_with("user-embed:processing", 1, stamped)


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_ack_failure_keeps_message_inflight_for_shutdown_requeue(
    mock_from_url: MagicMock, _mock_time: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 42})
    mock_client.brpoplpush.return_value = original
    pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    mock_client.lrem.side_effect = RuntimeError("redis down")
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    try:
        c.ack(42)
    except RuntimeError:
        pass

    stamped = _stamped({"user_id": 42})
    assert c.nack_all_inflight() == 1
    pipe_nack.lrem.assert_called_once_with("user-embed:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("user-embed", _nacked({"user_id": 42}))
    pipe_nack.execute.assert_called_once()


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_nack_requeues_payload(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 42})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    stamped = _stamped({"user_id": 42})
    pipe_nack.lrem.assert_called_once_with("user-embed:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("user-embed", _nacked({"user_id": 42}))
    pipe_nack.execute.assert_called_once()


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_nack_preserves_trace_id(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 42, "trace_id": "trace-ue-9"})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    expected = _nacked({"user_id": 42, "trace_id": "trace-ue-9"})
    pipe_nack.rpush.assert_called_once_with("user-embed", expected)


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_nack_sends_to_dlq_after_retry_limit(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"user_id": 42, "_retry_count": 5})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    stamped = _stamped({"user_id": 42, "_retry_count": 5})
    dlq_payload = json.dumps(
        {"user_id": 42, "_retry_count": 6, "_claimed_at": FIXED_TIME},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    pipe_nack.lrem.assert_called_once_with("user-embed:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("user-embed:dlq", dlq_payload)


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_reclaim_stuck_skips_fresh_items(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    import time
    fresh = json.dumps({"user_id": 1, "_claimed_at": time.time()})
    mock_client.lrange.return_value = [fresh]
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    mock_client.pipeline.assert_not_called()


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_reclaim_stuck_reclaims_expired_items(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    expired = json.dumps({"user_id": 2, "_claimed_at": 1.0})
    mock_client.lrange.return_value = [expired]
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    pipe.lrem.assert_called_once_with("user-embed:processing", 1, expired)
    pipe.rpush.assert_called_once_with("user-embed", expired)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_reclaim_stuck_reclaims_missing_claimed_at(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    legacy = json.dumps({"user_id": 3})
    mock_client.lrange.return_value = [legacy]
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    pipe.lrem.assert_called_once_with("user-embed:processing", 1, legacy)
    pipe.rpush.assert_called_once_with("user-embed", legacy)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_reclaim_stuck_mixed_fresh_and_expired(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    import time
    fresh = json.dumps({"user_id": 1, "_claimed_at": time.time()})
    expired = json.dumps({"user_id": 2, "_claimed_at": 1.0})
    mock_client.lrange.return_value = [fresh, expired]
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    assert mock_client.pipeline.call_count == 1
    pipe.lrem.assert_called_once_with("user-embed:processing", 1, expired)


@patch("ai_service.adapter.redis.user_embed_queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_nack_all_inflight_requeues_all_local_messages(
    mock_from_url: MagicMock, _mock_time: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe_s1, pipe_s2, pipe_n1, pipe_n2 = _make_pipe_side_effect(mock_client, 4)
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")
    raw1 = json.dumps({"user_id": 1})
    raw2 = json.dumps({"user_id": 2})
    mock_client.brpoplpush.side_effect = [raw1, raw2, None]

    assert c.pop_blocking(timeout_sec=1) == 1
    assert c.pop_blocking(timeout_sec=1) == 2

    assert c.nack_all_inflight() == 2

    stamped1 = _stamped({"user_id": 1})
    stamped2 = _stamped({"user_id": 2})
    assert pipe_n1.lrem.call_args_list == [call("user-embed:processing", 1, stamped1)]
    assert pipe_n2.lrem.call_args_list == [call("user-embed:processing", 1, stamped2)]
    assert pipe_n1.rpush.call_args_list == [call("user-embed", _nacked({"user_id": 1}))]
    assert pipe_n2.rpush.call_args_list == [call("user-embed", _nacked({"user_id": 2}))]

    c.ack(1)
    c.nack(2)
    assert mock_client.pipeline.call_count == 4  # unchanged


@patch("ai_service.adapter.redis.user_embed_queue.redis.from_url")
def test_nack_all_inflight_returns_zero_when_nothing_inflight(mock_from_url: MagicMock) -> None:
    mock_from_url.return_value = MagicMock()
    c = RedisUserEmbedQueueConsumer("redis://localhost:6379/0")

    assert c.nack_all_inflight() == 0
