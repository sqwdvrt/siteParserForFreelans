"""Тест user-embed consumer loop с моком очереди."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from ai_service.port.user_embed_queue import UserEmbedQueueConsumer
from ai_service.usecase.process_user_embed import ProcessUserEmbedUseCase
from ai_service.usecase.user_embed_consumer_loop import run_user_embed_consumer


class FakeUserEmbedQueueConsumer(UserEmbedQueueConsumer):
    """Очередь с заданными user_id. При pop возвращает по одному, затем None."""

    def __init__(self, user_ids: list[int], timeout_sec: int = 1) -> None:
        self._user_ids = list(user_ids)
        self._timeout = timeout_sec

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        if self._user_ids:
            return self._user_ids.pop(0)
        return None


class FlakyUserEmbedQueueConsumer(UserEmbedQueueConsumer):
    """Первая попытка pop — исключение, затем возвращается user_id."""

    def __init__(self) -> None:
        self._raised = False

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        if not self._raised:
            self._raised = True
            raise RuntimeError("redis temporary error")
        return 42


class ReliableFakeUserEmbedQueueConsumer(FakeUserEmbedQueueConsumer):
    def __init__(self, user_ids: list[int], timeout_sec: int = 1) -> None:
        super().__init__(user_ids, timeout_sec=timeout_sec)
        self.acked: list[int] = []
        self.nacked: list[int] = []

    def ack(self, user_id: int) -> None:
        self.acked.append(user_id)

    def nack(self, user_id: int) -> None:
        self.nacked.append(user_id)


class ShutdownAfterPopUserEmbedQueueConsumer(ReliableFakeUserEmbedQueueConsumer):
    def __init__(self, user_ids: list[int], stop_event: threading.Event) -> None:
        super().__init__(user_ids)
        self._stop_event = stop_event

    def pop_blocking(self, timeout_sec: int = 5) -> int | None:
        user_id = super().pop_blocking(timeout_sec=timeout_sec)
        if user_id is not None:
            self._stop_event.set()
        return user_id


def test_run_user_embed_consumer_calls_process() -> None:
    import time

    process = MagicMock(spec=ProcessUserEmbedUseCase)
    queue = FakeUserEmbedQueueConsumer([42])
    stop = threading.Event()

    def run() -> None:
        run_user_embed_consumer(queue, process, timeout_sec=1, stop_event=stop)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    process.execute.assert_called_once_with(42)


def test_run_user_embed_consumer_stops_on_event() -> None:
    process = MagicMock(spec=ProcessUserEmbedUseCase)
    queue = FakeUserEmbedQueueConsumer([1])
    stop = threading.Event()
    stop.set()

    run_user_embed_consumer(queue, process, timeout_sec=1, stop_event=stop)

    process.execute.assert_not_called()


def test_run_user_embed_consumer_recovers_after_pop_error() -> None:
    process = MagicMock(spec=ProcessUserEmbedUseCase)
    queue = FlakyUserEmbedQueueConsumer()
    stop = threading.Event()

    def _execute(user_id: int) -> None:
        stop.set()

    process.execute.side_effect = _execute

    run_user_embed_consumer(queue, process, timeout_sec=1, stop_event=stop)

    process.execute.assert_called_once_with(42)


def test_run_user_embed_consumer_acks_on_success() -> None:
    import time

    process = MagicMock(spec=ProcessUserEmbedUseCase)
    queue = ReliableFakeUserEmbedQueueConsumer([42])
    stop = threading.Event()

    def run() -> None:
        run_user_embed_consumer(queue, process, timeout_sec=1, stop_event=stop)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    assert queue.acked == [42]
    assert queue.nacked == []


def test_run_user_embed_consumer_nacks_on_process_error() -> None:
    import time

    process = MagicMock(spec=ProcessUserEmbedUseCase)
    process.execute.side_effect = RuntimeError("boom")
    queue = ReliableFakeUserEmbedQueueConsumer([42])
    stop = threading.Event()

    def run() -> None:
        run_user_embed_consumer(queue, process, timeout_sec=1, stop_event=stop)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    stop.set()
    t.join(timeout=3)

    assert queue.acked == []
    assert queue.nacked == [42]


def test_run_user_embed_consumer_requeues_message_if_shutdown_happens_after_pop() -> None:
    process = MagicMock(spec=ProcessUserEmbedUseCase)
    stop = threading.Event()
    queue = ShutdownAfterPopUserEmbedQueueConsumer([42], stop_event=stop)

    run_user_embed_consumer(queue, process, timeout_sec=1, stop_event=stop)

    process.execute.assert_not_called()
    assert queue.acked == []
    assert queue.nacked == [42]
