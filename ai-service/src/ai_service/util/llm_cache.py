"""LLM response cache backed by Redis.

Provides a simple TTL cache for Gemini API responses.  Keys are SHA-256
hashes of the prompt content so that identical requests hit the cache
while different prompts naturally miss.

Usage::

    cache = LLMCache(redis_client, ttl_seconds=86400)
    cached = cache.get("classifier", text)
    if cached is not None:
        return cached
    result = call_gemini_api(...)
    cache.set("classifier", text, result)
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Default TTL: 24 hours — long enough to cover re-processing of the same jobs
DEFAULT_TTL_SECONDS = int(os.getenv("LLM_CACHE_TTL_SECONDS", "86400"))
CACHE_KEY_PREFIX = "llm_cache"


def _make_key(pipeline: str, *parts: str) -> str:
    """Create a deterministic cache key from pipeline name and text parts."""
    raw = f"{pipeline}:{':'.join(str(p) for p in parts)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}:{pipeline}:{digest}"


class LLMCache:
    """Redis-backed cache for LLM responses."""

    def __init__(
        self,
        redis_client: Any | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    def get(self, pipeline: str, *key_parts: str) -> str | None:
        """Return cached response or None."""
        if self._redis is None:
            return None
        key = _make_key(pipeline, *key_parts)
        try:
            value = self._redis.get(key)
            if value is not None:
                logger.debug("llm_cache HIT key_prefix=%s", key[:40])
                return value.decode("utf-8") if isinstance(value, bytes) else value
        except Exception as exc:
            logger.warning("llm_cache get error: %s", exc)
        return None

    def set(self, pipeline: str, *key_parts: str, value: str) -> None:
        """Cache an LLM response."""
        if self._redis is None:
            return
        key = _make_key(pipeline, *key_parts)
        try:
            self._redis.setex(key, self._ttl, value)
            logger.debug("llm_cache SET key_prefix=%s ttl=%d", key[:40], self._ttl)
        except Exception as exc:
            logger.warning("llm_cache set error: %s", exc)

    def clear(self) -> int:
        """Delete all cache entries.  Returns count of deleted keys."""
        if self._redis is None:
            return 0
        try:
            keys = self._redis.keys(f"{CACHE_KEY_PREFIX}:*")
            if keys:
                return self._redis.delete(*keys)
        except Exception as exc:
            logger.warning("llm_cache clear error: %s", exc)
        return 0
