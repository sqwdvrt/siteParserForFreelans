"""Redis adapter: BRPOPLPUSH из user-rematch в processing, ack/nack по payload {"user_id": N}."""

from __future__ import annotations

from ai_service.adapter.redis.user_embed_queue import RedisUserEmbedQueueConsumer

DEFAULT_QUEUE = "user-rematch"


class RedisUserRematchQueueConsumer(RedisUserEmbedQueueConsumer):
    """Consumer очереди user-rematch через Redis BRPOP."""

    def __init__(self, redis_url: str, queue_name: str = DEFAULT_QUEUE) -> None:
        super().__init__(redis_url, queue_name=queue_name)
