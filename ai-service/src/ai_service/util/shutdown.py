"""Helpers for coordinating graceful shutdown timers."""

from __future__ import annotations

import logging
import math


def cap_blocking_pop_timeout(
    timeout_sec: int,
    grace_sec: float,
    *,
    logger: logging.Logger,
    timeout_name: str,
    grace_name: str,
) -> int:
    """Keep blocking queue waits short enough for graceful shutdown."""
    max_timeout_sec = max(1, math.ceil(grace_sec) - 1)
    if timeout_sec <= max_timeout_sec:
        return timeout_sec
    logger.warning(
        "%s=%ds exceeds graceful shutdown window %s=%.1fs; capping blocking pop timeout to %ds",
        timeout_name,
        timeout_sec,
        grace_name,
        grace_sec,
        max_timeout_sec,
    )
    return max_timeout_sec
