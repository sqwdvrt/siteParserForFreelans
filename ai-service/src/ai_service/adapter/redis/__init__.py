"""Redis adapters."""

from ai_service.adapter.redis.ac_batch_queue import ACBatchMessage, RedisACBatchQueueConsumer
from ai_service.adapter.redis.match_notify_queue import RedisMatchNotifyQueue
from ai_service.adapter.redis.queue import RedisQueueConsumer
from ai_service.adapter.redis.user_embed_queue import RedisUserEmbedQueueConsumer
from ai_service.adapter.redis.user_rematch_queue import RedisUserRematchQueue, RedisUserRematchQueueConsumer

__all__ = [
    "ACBatchMessage",
    "RedisACBatchQueueConsumer",
    "RedisMatchNotifyQueue",
    "RedisQueueConsumer",
    "RedisUserEmbedQueueConsumer",
    "RedisUserRematchQueue",
    "RedisUserRematchQueueConsumer",
]
