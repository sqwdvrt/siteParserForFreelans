"""Тест user-embed consumer loop с моком очереди."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

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
