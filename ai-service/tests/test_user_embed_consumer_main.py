"""Tests for ai-service cmd/user_embed_consumer/main.py shutdown behavior."""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path


def _load_user_embed_consumer_main_module():
    module_path = Path(__file__).resolve().parents[1] / "cmd" / "user_embed_consumer" / "main.py"
    spec = importlib.util.spec_from_file_location("user_embed_consumer_main_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shutdown_grace_sec_invalid_env_fallback(monkeypatch) -> None:
    module = _load_user_embed_consumer_main_module()
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "bad-value")

    assert module._shutdown_grace_sec() == module.DEFAULT_SHUTDOWN_GRACE_SEC


def test_main_forces_requeue_on_shutdown_timeout(monkeypatch, tmp_path: Path) -> None:
    module = _load_user_embed_consumer_main_module()

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
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "ProcessUserEmbedUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisUserEmbedQueueConsumer", lambda *_args, **_kwargs: queue_obj)

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

    def fake_run_consumer(queue, process_user_embed, *, timeout_sec: int, stop_event) -> None:
        _ = queue
        _ = process_user_embed
        _ = timeout_sec
        handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
        assert stop_event.wait(timeout=0.3)
        assert force_exit_event.wait(timeout=1.0)

    monkeypatch.setattr(module, "run_user_embed_consumer", fake_run_consumer)

    module.main()

    assert forced_exit_codes == [1]
    assert queue_obj.nack_all_inflight_calls == 2


def test_main_requeues_inflight_messages_on_clean_shutdown(monkeypatch, tmp_path: Path) -> None:
    module = _load_user_embed_consumer_main_module()

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
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "ProcessUserEmbedUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisUserEmbedQueueConsumer", lambda *_args, **_kwargs: queue_obj)
    monkeypatch.setattr(module.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "run_user_embed_consumer", lambda *_args, **_kwargs: None)

    module.main()

    assert queue_obj.nack_all_inflight_calls == 1


def test_main_drains_inflight_message_on_sigterm(monkeypatch, tmp_path: Path) -> None:
    module = _load_user_embed_consumer_main_module()

    ready_file = tmp_path / "ready"
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "1")
    monkeypatch.setenv(module.READY_FILE_ENV, str(ready_file))

    class FakeQueue:
        def __init__(self) -> None:
            self.nack_all_inflight_calls = 0

        def nack_all_inflight(self) -> int:
            self.nack_all_inflight_calls += 1
            return 0

    queue_obj = FakeQueue()
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "RedisUserEmbedQueueConsumer", lambda *_args, **_kwargs: queue_obj)

    execute_started = threading.Event()
    allow_execute_finish = threading.Event()
    execute_finished = threading.Event()

    class FakeProcessUserEmbed:
        def execute(self, _user_id: int) -> bool:
            execute_started.set()
            assert allow_execute_finish.wait(timeout=1.0)
            execute_finished.set()
            return True

    monkeypatch.setattr(module, "ProcessUserEmbedUseCase", lambda *_args, **_kwargs: FakeProcessUserEmbed())

    handlers: dict[int, object] = {}

    def fake_signal(sig, handler):
        handlers[sig] = handler

    monkeypatch.setattr(module.signal, "signal", fake_signal)

    forced_exit_codes: list[int] = []

    def fake_os_exit(code: int) -> None:
        forced_exit_codes.append(code)

    monkeypatch.setattr(module.os, "_exit", fake_os_exit)

    def fake_run_consumer(queue, process_user_embed, *, timeout_sec: int, stop_event) -> None:
        _ = queue
        _ = timeout_sec
        worker = threading.Thread(target=lambda: process_user_embed.execute(42))
        worker.start()
        assert execute_started.wait(timeout=1.0)
        assert ready_file.exists()
        handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
        assert stop_event.wait(timeout=0.3)
        assert not ready_file.exists()
        assert forced_exit_codes == []
        allow_execute_finish.set()
        worker.join(timeout=1.0)
        assert execute_finished.is_set()

    monkeypatch.setattr(module, "run_user_embed_consumer", fake_run_consumer)

    module.main()

    assert forced_exit_codes == []
    assert queue_obj.nack_all_inflight_calls == 1
