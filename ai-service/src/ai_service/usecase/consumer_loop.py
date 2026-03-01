"""Consumer loop: BRPOP → ProcessJob. Работает постоянно до stop_event."""

from __future__ import annotations

import logging
import threading

from ai_service.port.queue import JobQueueConsumer
from ai_service.usecase.process_job import ProcessJobUseCase
from ai_service.util.trace_context import reset_trace_id, set_trace_id

logger = logging.getLogger(__name__)


def _maybe_reclaim(queue: JobQueueConsumer) -> None:
    reclaim = getattr(queue, "reclaim_stuck", None)
    if callable(reclaim):
        reclaim()


def _maybe_ack(queue: JobQueueConsumer, job_id: int) -> None:
    ack = getattr(queue, "ack", None)
    if callable(ack):
        ack(job_id)


def _maybe_nack(queue: JobQueueConsumer, job_id: int) -> None:
    nack = getattr(queue, "nack", None)
    if callable(nack):
        nack(job_id)


def _maybe_trace_id(queue: JobQueueConsumer, job_id: int) -> str:
    trace_id = getattr(queue, "trace_id", None)
    if callable(trace_id):
        value = trace_id(job_id)
        if isinstance(value, str):
            return value
    return ""


def run_consumer(
    queue: JobQueueConsumer,
    process_job: ProcessJobUseCase,
    *,
    timeout_sec: int = 5,
    stop_event: threading.Event | None = None,
) -> None:
    """Цикл: BRPOP → ProcessJob. Выход по stop_event.set()."""
    stop = stop_event or threading.Event()
    _maybe_reclaim(queue)
    while not stop.is_set():
        try:
            job_id = queue.pop_blocking(timeout_sec=timeout_sec)
        except Exception as e:
            logger.exception("queue pop failed: %s", e)
            continue
        if job_id is not None:
            trace_id = _maybe_trace_id(queue, job_id)
            token = set_trace_id(trace_id)
            try:
                process_job.execute(job_id)
            except Exception as e:
                if trace_id:
                    logger.exception("process job_id=%s trace_id=%s failed: %s", job_id, trace_id, e)
                else:
                    logger.exception("process job_id=%s failed: %s", job_id, e)
                try:
                    _maybe_nack(queue, job_id)
                except Exception as nack_err:
                    if trace_id:
                        logger.exception("nack job_id=%s trace_id=%s failed: %s", job_id, trace_id, nack_err)
                    else:
                        logger.exception("nack job_id=%s failed: %s", job_id, nack_err)
            else:
                try:
                    _maybe_ack(queue, job_id)
                except Exception as ack_err:
                    if trace_id:
                        logger.exception("ack job_id=%s trace_id=%s failed: %s", job_id, trace_id, ack_err)
                    else:
                        logger.exception("ack job_id=%s failed: %s", job_id, ack_err)
            finally:
                reset_trace_id(token)
    logger.info("consumer loop stopped")
