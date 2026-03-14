from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

QUEUE_RETRY_DELAY_ENV = "AI_QUEUE_RETRY_DELAY_SEC"
DEFAULT_QUEUE_RETRY_DELAY_SEC = 5.0


def queue_retry_delay_sec() -> float:
    raw = os.getenv(QUEUE_RETRY_DELAY_ENV, str(DEFAULT_QUEUE_RETRY_DELAY_SEC))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_QUEUE_RETRY_DELAY_SEC
    if value < 0:
        return DEFAULT_QUEUE_RETRY_DELAY_SEC
    return value


def wait_before_retry(
    *,
    stop_event: threading.Event,
    logger: logging.Logger,
    operation: str,
    exc: Exception,
) -> bool:
    delay = queue_retry_delay_sec()
    message = str(exc)
    if "max requests limit exceeded" in message:
        logger.error(
            "%s failed: Redis request quota exhausted; retrying in %.1fs",
            operation,
            delay,
            exc_info=exc,
        )
    else:
        logger.exception("%s failed; retrying in %.1fs", operation, delay)
    return stop_event.wait(timeout=delay)


def reclaim_with_retry(
    *,
    reclaim: Callable[[], None] | None,
    stop_event: threading.Event,
    logger: logging.Logger,
    operation: str,
) -> bool:
    if reclaim is None:
        return True
    while not stop_event.is_set():
        try:
            reclaim()
            return True
        except Exception as exc:  # noqa: BLE001
            if wait_before_retry(
                stop_event=stop_event,
                logger=logger,
                operation=operation,
                exc=exc,
            ):
                return False
    return False
