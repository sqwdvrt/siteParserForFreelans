"""Тест consumer loop с моком очереди."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

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


class AckFailsQueueConsumer(ReliableFakeQueueConsumer):
    def ack(self, job_id: int) -> None:  # noqa: ARG002
        raise RuntimeError("ack failed")


class NackFailsQueueConsumer(ReliableFakeQueueConsumer):
    def nack(self, job_id: int) -> None:  # noqa: ARG002
        raise RuntimeError("nack failed")


class ReclaimQueueConsumer(ReliableFakeQueueConsumer):
    def __init__(self, job_ids: list[int]) -> None:
        super().__init__(job_ids)
        self.reclaimed = False

    def reclaim_stuck(self) -> None:
        self.reclaimed = True


class FlakyReclaimQueueConsumer(ReliableFakeQueueConsumer):
    def __init__(self, job_ids: list[int]) -> None:
        super().__init__(job_ids)
        self.reclaim_calls = 0

    def reclaim_stuck(self) -> None:
        self.reclaim_calls += 1
        if self.reclaim_calls == 1:
            raise RuntimeError("redis temporary error")


class ShutdownAfterPopQueueConsumer(ReliableFakeQueueConsumer):
    def __init__(self, job_ids: list[int], stop_event: threading.Event) -> None:
        super().__init__(job_ids)
        self._stop_event = stop_event

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        job_id = super().pop_blocking(timeout_sec=timeout_sec)
        if job_id is not None:
            self._stop_event.set()
        return job_id


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


def test_run_consumer_calls_reclaim_if_available() -> None:
    import time

    process_job = MagicMock(spec=ProcessJobUseCase)
    queue = ReclaimQueueConsumer([])
    stop = threading.Event()

    t = threading.Thread(target=lambda: run_consumer(queue, process_job, timeout_sec=1, stop_event=stop))
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(timeout=3)

    assert queue.reclaimed is True


def test_run_consumer_retries_reclaim_error(monkeypatch) -> None:
    process_job = MagicMock(spec=ProcessJobUseCase)
    queue = FlakyReclaimQueueConsumer([42])
    stop = threading.Event()

    monkeypatch.setenv("AI_QUEUE_RETRY_DELAY_SEC", "0")

    def _execute(job_id: int) -> None:
        stop.set()

    process_job.execute.side_effect = _execute

    run_consumer(queue, process_job, timeout_sec=1, stop_event=stop)

    assert queue.reclaim_calls == 2
    process_job.execute.assert_called_once_with(42)


def test_run_consumer_continues_when_ack_raises() -> None:
    import time

    process_job = MagicMock(spec=ProcessJobUseCase)
    queue = AckFailsQueueConsumer([42])
    stop = threading.Event()

    t = threading.Thread(target=lambda: run_consumer(queue, process_job, timeout_sec=1, stop_event=stop))
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    process_job.execute.assert_called_once_with(42)


def test_run_consumer_continues_when_nack_raises() -> None:
    import time

    process_job = MagicMock(spec=ProcessJobUseCase)
    process_job.execute.side_effect = RuntimeError("boom")
    queue = NackFailsQueueConsumer([42])
    stop = threading.Event()

    t = threading.Thread(target=lambda: run_consumer(queue, process_job, timeout_sec=1, stop_event=stop))
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    process_job.execute.assert_called_once_with(42)


def test_run_consumer_requeues_job_if_shutdown_happens_after_pop() -> None:
    process_job = MagicMock(spec=ProcessJobUseCase)
    stop = threading.Event()
    queue = ShutdownAfterPopQueueConsumer([42], stop_event=stop)

    run_consumer(queue, process_job, timeout_sec=1, stop_event=stop)

    process_job.execute.assert_not_called()
    assert queue.acked == []
    assert queue.nacked == [42]
