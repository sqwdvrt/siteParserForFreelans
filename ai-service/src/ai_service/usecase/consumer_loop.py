"""Consumer loop: BRPOP → ProcessJob. Работает постоянно до stop_event."""

from __future__ import annotations

import logging
import threading
from ai_service.port.queue import JobQueueConsumer
from ai_service.usecase.process_job import ProcessJobUseCase

logger = logging.getLogger(__name__)


def run_consumer(
    queue: JobQueueConsumer,
    process_job: ProcessJobUseCase,
    *,
    timeout_sec: int = 5,
    stop_event: threading.Event | None = None,
) -> None:
    """Цикл: BRPOP → ProcessJob. Выход по stop_event.set()."""
    stop = stop_event or threading.Event()
    while not stop.is_set():
        job_id = queue.pop_blocking(timeout_sec=timeout_sec)
        if job_id is not None:
            try:
                process_job.execute(job_id)
            except Exception as e:
                logger.exception("process job_id=%s failed: %s", job_id, e)
    logger.info("consumer loop stopped")
