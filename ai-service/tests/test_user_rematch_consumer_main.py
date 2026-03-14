"""Tests for ai-service cmd/user_rematch_consumer/main.py shutdown behavior."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_service.usecase.process_user_rematch import ProcessUserRematchUseCase


def _load_user_rematch_consumer_main_module():
    module_path = Path(__file__).resolve().parents[1] / "cmd" / "user_rematch_consumer" / "main.py"
    spec = importlib.util.spec_from_file_location("user_rematch_consumer_main_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shutdown_grace_sec_invalid_env_fallback(monkeypatch) -> None:
    module = _load_user_rematch_consumer_main_module()
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "bad-value")

    assert module._shutdown_grace_sec() == module.DEFAULT_SHUTDOWN_GRACE_SEC


def test_main_rejects_missing_redis_url_in_production(monkeypatch, tmp_path: Path) -> None:
    module = _load_user_rematch_consumer_main_module()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db?sslmode=require")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))

    with pytest.raises(SystemExit):
        module.main()


def test_run_consumer_requeues_message_if_shutdown_happens_after_pop() -> None:
    module = _load_user_rematch_consumer_main_module()
    process = MagicMock(spec=ProcessUserRematchUseCase)
    stop = threading.Event()

    class FakeQueue:
        def __init__(self) -> None:
            self.nacked: list[int] = []
            self.acked: list[int] = []
            self._popped = False

        def reclaim_stuck(self) -> None:
            return None

        def pop_blocking(self, timeout_sec: int = 5) -> int | None:
            _ = timeout_sec
            if self._popped:
                return None
            self._popped = True
            stop.set()
            return 42

        def ack(self, user_id: int) -> None:
            self.acked.append(user_id)

        def nack(self, user_id: int) -> None:
            self.nacked.append(user_id)

    queue = FakeQueue()

    module._run_consumer(queue, process, timeout_sec=1, stop_event=stop)

    process.execute.assert_not_called()
    assert queue.acked == []
    assert queue.nacked == [42]


def test_run_consumer_retries_reclaim_error(monkeypatch) -> None:
    module = _load_user_rematch_consumer_main_module()
    process = MagicMock(spec=ProcessUserRematchUseCase)
    stop = threading.Event()

    monkeypatch.setenv("AI_QUEUE_RETRY_DELAY_SEC", "0")

    class FakeQueue:
        def __init__(self) -> None:
            self.reclaim_calls = 0

        def reclaim_stuck(self) -> None:
            self.reclaim_calls += 1
            if self.reclaim_calls == 1:
                raise RuntimeError("redis temporary error")

        def pop_blocking(self, timeout_sec: int = 5) -> int | None:
            _ = timeout_sec
            stop.set()
            return None

    queue = FakeQueue()

    module._run_consumer(queue, process, timeout_sec=1, stop_event=stop)

    assert queue.reclaim_calls == 2
    process.execute.assert_not_called()


def test_main_forces_requeue_on_shutdown_timeout(monkeypatch, tmp_path: Path) -> None:
    module = _load_user_rematch_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "0.05")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))

    class FakeQueue:
        def __init__(self) -> None:
            self.nack_all_inflight_calls = 0

        def nack_all_inflight(self) -> int:
            self.nack_all_inflight_calls += 1
            return 1

    queue_obj = FakeQueue()
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresMatchRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisMatchNotifyQueue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "ProcessUserRematchUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisUserRematchQueueConsumer", lambda *_args, **_kwargs: queue_obj)
    monkeypatch.setattr(module, "start_health_server", lambda *_args, **_kwargs: object())

    handlers: dict[int, object] = {}

    def fake_signal(sig, handler):
        handlers[sig] = handler

    monkeypatch.setattr(module.signal, "signal", fake_signal)

    forced_exit_codes: list[int] = []
    force_exit_event = threading.Event()

    def fake_os_exit(code: int) -> None:
        forced_exit_codes.append(code)
        force_exit_event.set()

    monkeypatch.setattr(module.os, "_exit", fake_os_exit)

    def fake_run_consumer(queue, process_user_rematch, *, timeout_sec: int, stop_event) -> None:
        _ = queue
        _ = process_user_rematch
        _ = timeout_sec
        handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
        assert stop_event.wait(timeout=0.3)
        assert force_exit_event.wait(timeout=1.0)

    monkeypatch.setattr(module, "_run_consumer", fake_run_consumer)

    module.main()

    assert forced_exit_codes == [1]
    assert queue_obj.nack_all_inflight_calls == 2


def test_main_requeues_inflight_messages_on_clean_shutdown(monkeypatch, tmp_path: Path) -> None:
    module = _load_user_rematch_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))

    class FakeQueue:
        def __init__(self) -> None:
            self.nack_all_inflight_calls = 0

        def nack_all_inflight(self) -> int:
            self.nack_all_inflight_calls += 1
            return 1

    queue_obj = FakeQueue()
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresMatchRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisMatchNotifyQueue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "ProcessUserRematchUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisUserRematchQueueConsumer", lambda *_args, **_kwargs: queue_obj)
    monkeypatch.setattr(module, "start_health_server", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_run_consumer", lambda *_args, **_kwargs: None)

    module.main()

    assert queue_obj.nack_all_inflight_calls == 1
