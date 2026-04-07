"""Feature flags with traffic splitting for A/B testing."""
import os
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class FeatureFlag:
    name: str
    enabled: bool = True
    rollout_percentage: float = 100.0
    variants: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class FeatureFlags:
    """Simple feature flags with deterministic traffic splitting."""

    def __init__(self):
        self._flags: dict[str, FeatureFlag] = {}
        self._load_from_env()

    def _load_from_env(self):
        """Load flags from FEATURE_FLAGS_JSON env var."""
        flags_json = os.getenv("FEATURE_FLAGS_JSON", "{}")
        try:
            flags_data = json.loads(flags_json)
            for name, config in flags_data.items():
                self._flags[name] = FeatureFlag(
                    name=name,
                    enabled=config.get("enabled", True),
                    rollout_percentage=config.get("rollout_percentage", 100.0),
                    variants=config.get("variants", {}),
                    metadata=config.get("metadata", {}),
                )
            if flags_data:
                logger.info("Feature flags loaded: %s", list(flags_data.keys()))
        except json.JSONDecodeError:
            logger.warning("Invalid FEATURE_FLAGS_JSON: %s", flags_json)

    def is_enabled(self, flag_name: str, user_id: int | None = None) -> bool:
        """Check if a flag is enabled for a user."""
        flag = self._flags.get(flag_name)
        if not flag or not flag.enabled:
            return False

        if flag.rollout_percentage < 100.0:
            if user_id is not None:
                hash_val = int(
                    hashlib.md5(f"{flag_name}:{user_id}".encode()).hexdigest(), 16
                ) % 100
                return hash_val < flag.rollout_percentage
            else:
                return random.random() * 100 < flag.rollout_percentage

        return True

    def get_variant(self, flag_name: str, user_id: int | None = None) -> str | None:
        """Get the variant for a user based on traffic split."""
        flag = self._flags.get(flag_name)
        if not flag or not flag.enabled or not flag.variants:
            return None

        if user_id is not None:
            hash_val = int(
                hashlib.md5(f"{flag_name}:variant:{user_id}".encode()).hexdigest(), 16
            ) % 100
        else:
            hash_val = int(random.random() * 100)

        cumulative = 0.0
        for variant, percentage in flag.variants.items():
            cumulative += percentage
            if hash_val < cumulative:
                return variant

        return list(flag.variants.keys())[-1]

    def get_flag(self, flag_name: str) -> FeatureFlag | None:
        return self._flags.get(flag_name)

    def list_flags(self) -> dict[str, dict]:
        return {
            name: {
                "enabled": flag.enabled,
                "rollout_percentage": flag.rollout_percentage,
                "variants": flag.variants,
                "metadata": flag.metadata,
            }
            for name, flag in self._flags.items()
        }


# Global instance
_flags: FeatureFlags | None = None


def get_flags() -> FeatureFlags:
    global _flags
    if _flags is None:
        _flags = FeatureFlags()
    return _flags


def reset_flags():
    """For testing."""
    global _flags
    _flags = None


def flags_response() -> dict:
    """Return current flags as dict for HTTP response."""
    return {"flags": get_flags().list_flags()}
