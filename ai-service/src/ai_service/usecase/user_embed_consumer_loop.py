"""Consumer loop: BRPOP user-embed → ProcessUserEmbed. Работает до stop_event."""

from __future__ import annotations

import logging
import threading

from ai_service.port.user_embed_queue import UserEmbedQueueConsumer
from ai_service.usecase.process_user_embed import ProcessUserEmbedUseCase

logger = logging.getLogger(__name__)


def run_user_embed_consumer(
    queue: UserEmbedQueueConsumer,
    process_user_embed: ProcessUserEmbedUseCase,
    *,
    timeout_sec: int = 5,
    stop_event: threading.Event | None = None,
) -> None:
    """Цикл: BRPOP user-embed → ProcessUserEmbed. Выход по stop_event.set()."""
    stop = stop_event or threading.Event()
    while not stop.is_set():
        try:
            user_id = queue.pop_blocking(timeout_sec=timeout_sec)
        except Exception as e:
            logger.exception("user-embed queue pop failed: %s", e)
            continue
        if user_id is not None:
            try:
                process_user_embed.execute(user_id)
            except Exception as e:
                logger.exception("process user_id=%s failed: %s", user_id, e)
    logger.info("user-embed consumer loop stopped")
