"""Тесты RedisQueueConsumer (мок Redis)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

from ai_service.adapter.redis import RedisQueueConsumer

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
    """Return a list of fresh pipe mocks, wired up as side_effect on mock_client.pipeline."""
    pipes = [MagicMock() for _ in range(count)]
    mock_client.pipeline.side_effect = pipes
    return pipes


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_returns_job_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    mock_client.brpoplpush.return_value = json.dumps({"job_id": 42})
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_exposes_trace_id(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    raw = json.dumps({"job_id": 42, "trace_id": "trace-abc"})
    mock_client.brpoplpush.return_value = raw
    c = RedisQueueConsumer("redis://localhost:6379/0")

    assert c.pop_blocking(timeout_sec=1) == 42
    assert c.trace_id(42) == "trace-abc"


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


@patch("ai_service.adapter.redis.queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_pop_blocking_injects_claimed_at(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"job_id": 42})
    mock_client.brpoplpush.return_value = original
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    c = RedisQueueConsumer("redis://localhost:6379/0")

    assert c.pop_blocking(timeout_sec=1) == 42

    stamped = _stamped({"job_id": 42})
    pipe.lrem.assert_called_once_with("ai-process:processing", 1, original)
    pipe.lpush.assert_called_once_with("ai-process:processing", stamped)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_ack_removes_from_processing(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"job_id": 42})
    mock_client.brpoplpush.return_value = original
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.ack(42)

    stamped = _stamped({"job_id": 42})
    mock_client.lrem.assert_called_once_with("ai-process:processing", 1, stamped)


@patch("ai_service.adapter.redis.queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_ack_failure_keeps_message_inflight_for_shutdown_requeue(
    mock_from_url: MagicMock, _mock_time: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"job_id": 42})
    mock_client.brpoplpush.return_value = original
    pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    mock_client.lrem.side_effect = RuntimeError("redis down")
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    try:
        c.ack(42)
    except RuntimeError:
        pass

    stamped = _stamped({"job_id": 42})
    assert c.nack_all_inflight() == 1
    pipe_nack.lrem.assert_called_once_with("ai-process:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("ai-process", _nacked({"job_id": 42}))
    pipe_nack.execute.assert_called_once()


@patch("ai_service.adapter.redis.queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_nack_requeues_payload(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"job_id": 42})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    stamped = _stamped({"job_id": 42})
    pipe_nack.lrem.assert_called_once_with("ai-process:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("ai-process", _nacked({"job_id": 42}))
    pipe_nack.execute.assert_called_once()


@patch("ai_service.adapter.redis.queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_nack_preserves_trace_id(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"job_id": 42, "trace_id": "trace-123"})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    expected = _nacked({"job_id": 42, "trace_id": "trace-123"})
    pipe_nack.rpush.assert_called_once_with("ai-process", expected)


@patch("ai_service.adapter.redis.queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_nack_sends_to_dlq_after_retry_limit(mock_from_url: MagicMock, _mock_time: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    original = json.dumps({"job_id": 42, "_retry_count": 5})
    mock_client.brpoplpush.return_value = original
    _pipe_stamp, pipe_nack = _make_pipe_side_effect(mock_client, 2)
    c = RedisQueueConsumer("redis://localhost:6379/0")
    assert c.pop_blocking(timeout_sec=1) == 42

    c.nack(42)

    stamped = _stamped({"job_id": 42, "_retry_count": 5})
    dlq_payload = json.dumps(
        {"job_id": 42, "_retry_count": 6, "_claimed_at": FIXED_TIME},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    pipe_nack.lrem.assert_called_once_with("ai-process:processing", 1, stamped)
    pipe_nack.rpush.assert_called_once_with("ai-process:dlq", dlq_payload)


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_reclaim_stuck_skips_fresh_items(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    import time
    fresh = json.dumps({"job_id": 1, "_claimed_at": time.time()})
    mock_client.lrange.return_value = [fresh]
    c = RedisQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    mock_client.pipeline.assert_not_called()


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_reclaim_stuck_reclaims_expired_items(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    expired = json.dumps({"job_id": 2, "_claimed_at": 1.0})  # epoch second 1 = very old
    mock_client.lrange.return_value = [expired]
    c = RedisQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    pipe.lrem.assert_called_once_with("ai-process:processing", 1, expired)
    pipe.rpush.assert_called_once_with("ai-process", expired)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_reclaim_stuck_reclaims_missing_claimed_at(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    legacy = json.dumps({"job_id": 3})  # no _claimed_at
    mock_client.lrange.return_value = [legacy]
    c = RedisQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    pipe.lrem.assert_called_once_with("ai-process:processing", 1, legacy)
    pipe.rpush.assert_called_once_with("ai-process", legacy)
    pipe.execute.assert_called_once()


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_reclaim_stuck_mixed_fresh_and_expired(mock_from_url: MagicMock) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    pipe = MagicMock()
    mock_client.pipeline.return_value = pipe
    import time
    fresh = json.dumps({"job_id": 1, "_claimed_at": time.time()})
    expired = json.dumps({"job_id": 2, "_claimed_at": 1.0})
    mock_client.lrange.return_value = [fresh, expired]
    c = RedisQueueConsumer("redis://localhost:6379/0")

    c.reclaim_stuck()

    assert mock_client.pipeline.call_count == 1
    pipe.lrem.assert_called_once_with("ai-process:processing", 1, expired)


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


@patch("ai_service.adapter.redis.queue.time.time", return_value=FIXED_TIME)
@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_nack_all_inflight_requeues_all_local_messages(
    mock_from_url: MagicMock, _mock_time: MagicMock
) -> None:
    mock_client = MagicMock()
    mock_from_url.return_value = mock_client
    # 2 pops (pipe_s1, pipe_s2) + 2 nack_all (pipe_n1, pipe_n2)
    pipe_s1, pipe_s2, pipe_n1, pipe_n2 = _make_pipe_side_effect(mock_client, 4)
    c = RedisQueueConsumer("redis://localhost:6379/0")
    raw1 = json.dumps({"job_id": 1})
    raw2 = json.dumps({"job_id": 2})
    mock_client.brpoplpush.side_effect = [raw1, raw2, None]

    assert c.pop_blocking(timeout_sec=1) == 1
    assert c.pop_blocking(timeout_sec=1) == 2

    assert c.nack_all_inflight() == 2

    stamped1 = _stamped({"job_id": 1})
    stamped2 = _stamped({"job_id": 2})
    assert pipe_n1.lrem.call_args_list == [call("ai-process:processing", 1, stamped1)]
    assert pipe_n2.lrem.call_args_list == [call("ai-process:processing", 1, stamped2)]
    assert pipe_n1.rpush.call_args_list == [call("ai-process", _nacked({"job_id": 1}))]
    assert pipe_n2.rpush.call_args_list == [call("ai-process", _nacked({"job_id": 2}))]
    assert pipe_n1.execute.call_count == 1
    assert pipe_n2.execute.call_count == 1

    c.ack(1)
    c.nack(2)
    # After nack_all_inflight cleared inflight, ack/nack should be no-ops
    assert mock_client.pipeline.call_count == 4  # unchanged


@patch("ai_service.adapter.redis.queue.redis.from_url")
def test_nack_all_inflight_returns_zero_when_nothing_inflight(mock_from_url: MagicMock) -> None:
    mock_from_url.return_value = MagicMock()
    c = RedisQueueConsumer("redis://localhost:6379/0")

    assert c.nack_all_inflight() == 0
