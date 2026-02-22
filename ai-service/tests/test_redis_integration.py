"""Integration tests for Redis adapters against a real Redis instance."""

from __future__ import annotations

import json
import os
import uuid

import pytest
import redis

from ai_service.adapter.redis import (
    RedisMatchNotifyQueue,
    RedisQueueConsumer,
    RedisUserEmbedQueueConsumer,
)
from ai_service.port.match_repository import MatchCandidate

pytestmark = pytest.mark.integration


@pytest.fixture
def redis_url() -> str:
    value = os.getenv("REDIS_URL")
    if not value:
        pytest.fail("REDIS_URL not set; integration tests require Redis")
    return value


@pytest.fixture
def redis_client(redis_url: str) -> redis.Redis:
    client = redis.from_url(redis_url, decode_responses=True)
    try:
        client.ping()
    except Exception as e:
        pytest.fail(f"failed to connect to Redis: {e}")
    return client


@pytest.fixture
def queue_prefix() -> str:
    return f"it:{uuid.uuid4().hex}"


@pytest.fixture(autouse=True)
def cleanup_queues(redis_client: redis.Redis, queue_prefix: str):
    try:
        yield
    finally:
        for key in redis_client.scan_iter(f"{queue_prefix}*"):
            redis_client.delete(key)


def test_redis_queue_ack_flow(redis_url: str, redis_client: redis.Redis, queue_prefix: str) -> None:
    queue_name = f"{queue_prefix}:ai-process"
    consumer = RedisQueueConsumer(redis_url, queue_name=queue_name)
    redis_client.lpush(queue_name, json.dumps({"job_id": 42}))

    assert consumer.pop_blocking(timeout_sec=1) == 42
    assert redis_client.llen(f"{queue_name}:processing") == 1

    consumer.ack(42)

    assert redis_client.llen(f"{queue_name}:processing") == 0


def test_redis_queue_nack_moves_to_dlq(redis_url: str, redis_client: redis.Redis, queue_prefix: str) -> None:
    queue_name = f"{queue_prefix}:ai-process"
    consumer = RedisQueueConsumer(redis_url, queue_name=queue_name)
    redis_client.lpush(queue_name, json.dumps({"job_id": 42, "_retry_count": 5}))

    assert consumer.pop_blocking(timeout_sec=1) == 42
    consumer.nack(42)

    dlq_raw = redis_client.lpop(f"{queue_name}:dlq")
    assert dlq_raw is not None
    assert json.loads(dlq_raw) == {"job_id": 42, "_retry_count": 6}
    assert redis_client.llen(f"{queue_name}:processing") == 0


def test_user_embed_reclaim_stuck(redis_url: str, redis_client: redis.Redis, queue_prefix: str) -> None:
    queue_name = f"{queue_prefix}:user-embed"
    consumer = RedisUserEmbedQueueConsumer(redis_url, queue_name=queue_name)
    redis_client.rpush(f"{queue_name}:processing", json.dumps({"user_id": 7}))

    consumer.reclaim_stuck()

    assert redis_client.llen(f"{queue_name}:processing") == 0
    raw = redis_client.rpop(queue_name)
    assert raw is not None
    assert json.loads(raw) == {"user_id": 7}


def test_match_notify_enqueue(redis_url: str, redis_client: redis.Redis, queue_prefix: str) -> None:
    queue_name = f"{queue_prefix}:match-notify"
    queue = RedisMatchNotifyQueue(redis_url, queue_name=queue_name)

    queue.enqueue(MatchCandidate(user_id=11, job_id=22, match_score=0.87654))

    raw = redis_client.rpop(queue_name)
    assert raw is not None
    assert json.loads(raw) == {"user_id": 11, "job_id": 22, "match_score": 0.8765}
