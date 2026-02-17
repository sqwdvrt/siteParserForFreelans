"""Тест consumer loop с моком очереди."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from ai_service.port.queue import JobQueueConsumer
from ai_service.usecase.consumer_loop import run_consumer
from ai_service.usecase.process_job import ProcessJobUseCase


class FakeQueueConsumer(JobQueueConsumer):
    """Очередь с заданными job_id. При pop возвращает по одному, затем None."""

    def __init__(self, job_ids: list[int], timeout_sec: int = 1) -> None:
        self._job_ids = list(job_ids)
        self._timeout = timeout_sec

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        if self._job_ids:
            return self._job_ids.pop(0)
        return None


class FlakyQueueConsumer(JobQueueConsumer):
    """Первая попытка pop — исключение, затем возвращается job_id."""

    def __init__(self) -> None:
        self._raised = False

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        if not self._raised:
            self._raised = True
            raise RuntimeError("redis temporary error")
        return 42


class ReliableFakeQueueConsumer(FakeQueueConsumer):
    def __init__(self, job_ids: list[int], timeout_sec: int = 1) -> None:
        super().__init__(job_ids, timeout_sec=timeout_sec)
        self.acked: list[int] = []
        self.nacked: list[int] = []

    def ack(self, job_id: int) -> None:
        self.acked.append(job_id)

    def nack(self, job_id: int) -> None:
        self.nacked.append(job_id)


def test_run_consumer_calls_process_job() -> None:
    import time

    process_job = MagicMock(spec=ProcessJobUseCase)
    queue = FakeQueueConsumer([42])
    stop = threading.Event()

    def run() -> None:
        run_consumer(queue, process_job, timeout_sec=1, stop_event=stop)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    process_job.execute.assert_called_once_with(42)


def test_run_consumer_stops_on_event() -> None:
    process_job = MagicMock(spec=ProcessJobUseCase)
    queue = FakeQueueConsumer([1])
    stop = threading.Event()
    stop.set()

    run_consumer(queue, process_job, timeout_sec=1, stop_event=stop)

    process_job.execute.assert_not_called()


def test_run_consumer_recovers_after_pop_error() -> None:
    process_job = MagicMock(spec=ProcessJobUseCase)
    queue = FlakyQueueConsumer()
    stop = threading.Event()

    def _execute(job_id: int) -> None:
        stop.set()

    process_job.execute.side_effect = _execute

    run_consumer(queue, process_job, timeout_sec=1, stop_event=stop)

    process_job.execute.assert_called_once_with(42)


def test_run_consumer_acks_on_success() -> None:
    import time

    process_job = MagicMock(spec=ProcessJobUseCase)
    queue = ReliableFakeQueueConsumer([42])
    stop = threading.Event()

    def run() -> None:
        run_consumer(queue, process_job, timeout_sec=1, stop_event=stop)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    assert queue.acked == [42]
    assert queue.nacked == []


def test_run_consumer_nacks_on_process_error() -> None:
    import time

    process_job = MagicMock(spec=ProcessJobUseCase)
    process_job.execute.side_effect = RuntimeError("boom")
    queue = ReliableFakeQueueConsumer([42])
    stop = threading.Event()

    def run() -> None:
        run_consumer(queue, process_job, timeout_sec=1, stop_event=stop)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    assert queue.acked == []
    assert queue.nacked == [42]
