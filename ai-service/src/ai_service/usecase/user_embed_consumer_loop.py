"""Consumer loop: BRPOP user-embed → ProcessUserEmbed. Работает до stop_event."""

from __future__ import annotations

import logging
import threading

from ai_service.port.user_embed_queue import UserEmbedQueueConsumer
from ai_service.usecase.process_user_embed import ProcessUserEmbedUseCase
from ai_service.util.trace_context import reset_trace_id, set_trace_id

logger = logging.getLogger(__name__)


def _maybe_reclaim(queue: UserEmbedQueueConsumer) -> None:
    reclaim = getattr(queue, "reclaim_stuck", None)
    if callable(reclaim):
        reclaim()


def _maybe_ack(queue: UserEmbedQueueConsumer, user_id: int) -> None:
    ack = getattr(queue, "ack", None)
    if callable(ack):
        ack(user_id)


def _maybe_nack(queue: UserEmbedQueueConsumer, user_id: int) -> None:
    nack = getattr(queue, "nack", None)
    if callable(nack):
        nack(user_id)


def _maybe_shutdown_requeue(queue: UserEmbedQueueConsumer, user_id: int, stop: threading.Event) -> bool:
    if not stop.is_set():
        return False
    try:
        _maybe_nack(queue, user_id)
    except Exception as nack_err:
        logger.exception("shutdown nack user_id=%s failed: %s", user_id, nack_err)
    return True


def _maybe_trace_id(queue: UserEmbedQueueConsumer, user_id: int) -> str:
    trace_id = getattr(queue, "trace_id", None)
    if callable(trace_id):
        value = trace_id(user_id)
        if isinstance(value, str):
            return value
    return ""


def run_user_embed_consumer(
    queue: UserEmbedQueueConsumer,
    process_user_embed: ProcessUserEmbedUseCase,
    *,
    timeout_sec: int = 5,
    stop_event: threading.Event | None = None,
) -> None:
    """Цикл: BRPOP user-embed → ProcessUserEmbed. Выход по stop_event.set()."""
    stop = stop_event or threading.Event()
    _maybe_reclaim(queue)
    while not stop.is_set():
        try:
            user_id = queue.pop_blocking(timeout_sec=timeout_sec)
        except Exception as e:
            logger.exception("user-embed queue pop failed: %s", e)
            continue
        if user_id is not None:
            if _maybe_shutdown_requeue(queue, user_id, stop):
                break
            trace_id = _maybe_trace_id(queue, user_id)
            token = set_trace_id(trace_id)
            try:
                process_user_embed.execute(user_id)
            except Exception as e:
                if trace_id:
                    logger.exception("process user_id=%s trace_id=%s failed: %s", user_id, trace_id, e)
                else:
                    logger.exception("process user_id=%s failed: %s", user_id, e)
                try:
                    _maybe_nack(queue, user_id)
                except Exception as nack_err:
                    if trace_id:
                        logger.exception("nack user_id=%s trace_id=%s failed: %s", user_id, trace_id, nack_err)
                    else:
                        logger.exception("nack user_id=%s failed: %s", user_id, nack_err)
            else:
                try:
                    _maybe_ack(queue, user_id)
                except Exception as ack_err:
                    if trace_id:
                        logger.exception("ack user_id=%s trace_id=%s failed: %s", user_id, trace_id, ack_err)
                    else:
                        logger.exception("ack user_id=%s failed: %s", user_id, ack_err)
            finally:
                reset_trace_id(token)
    logger.info("user-embed consumer loop stopped")
