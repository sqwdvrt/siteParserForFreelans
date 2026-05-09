"""Tests for ai-service cmd/consumer/main.py shutdown behavior."""

from __future__ import annotations

import builtins
import importlib.util
import sys
import threading
from pathlib import Path

import pytest


def _load_consumer_main_module():
    module_path = Path(__file__).resolve().parents[1] / "cmd" / "consumer" / "main.py"
    spec = importlib.util.spec_from_file_location("consumer_main_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_debug_server(monkeypatch, module) -> None:
    class FakeDebugServer:
        def serve_forever(self) -> None:
            return

        def shutdown(self) -> None:
            return

        def server_close(self) -> None:
            return

    monkeypatch.setattr(module, "start_debug_http_server", lambda *_args, **_kwargs: FakeDebugServer())


def test_main_imports_without_gemini_when_classifier_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_GEMINI_CLASSIFIER", "0")
    for module_name in list(sys.modules):
        if module_name == "ai_service.adapter.gemini" or module_name.startswith("ai_service.adapter.gemini."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ai_service.adapter.gemini" or name.startswith("ai_service.adapter.gemini."):
            raise ModuleNotFoundError("No module named 'ai_service.adapter.gemini'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    module = _load_consumer_main_module()

    assert module._classifier_enabled() is False


def test_shutdown_grace_sec_invalid_env_fallback(monkeypatch) -> None:
    module = _load_consumer_main_module()
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "bad-value")

    assert module._shutdown_grace_sec() == module.DEFAULT_SHUTDOWN_GRACE_SEC


def test_main_caps_pop_timeout_to_shutdown_grace(monkeypatch, tmp_path: Path) -> None:
    module = _load_consumer_main_module()
    _patch_debug_server(monkeypatch, module)

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "20")
    monkeypatch.setenv(module.POP_TIMEOUT_SEC_ENV, "60")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))

    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresMatchRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFilterEventRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "AccumulateMatchesUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "CrossEncoderReranker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "ProcessJobUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisQueueConsumer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module.signal, "signal", lambda *_args, **_kwargs: None)

    seen_timeout: list[int] = []

    def fake_run_consumer(_queue, _process_job, *, timeout_sec: int, stop_event) -> None:
        _ = stop_event
        seen_timeout.append(timeout_sec)

    monkeypatch.setattr(module, "run_consumer", fake_run_consumer)

    module.main()

    assert seen_timeout == [19]


def test_main_exits_on_missing_database_url(monkeypatch, tmp_path: Path) -> None:
    module = _load_consumer_main_module()
    _patch_debug_server(monkeypatch, module)

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))

    with pytest.raises(SystemExit):
        module.main()


def test_main_forces_requeue_on_shutdown_timeout(monkeypatch, tmp_path: Path) -> None:
    module = _load_consumer_main_module()
    _patch_debug_server(monkeypatch, module)

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
    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresMatchRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFilterEventRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "AccumulateMatchesUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "CrossEncoderReranker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "ProcessJobUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisQueueConsumer", lambda *_args, **_kwargs: queue_obj)

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

    def fake_run_consumer(queue, process_job, *, timeout_sec: int, stop_event) -> None:
        _ = process_job
        _ = timeout_sec
        handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
        assert stop_event.wait(timeout=0.3)
        assert force_exit_event.wait(timeout=1.0)

    monkeypatch.setattr(module, "run_consumer", fake_run_consumer)

    module.main()

    assert forced_exit_codes == [1]
    assert queue_obj.nack_all_inflight_calls == 2


def test_main_requeues_inflight_messages_on_clean_shutdown(monkeypatch, tmp_path: Path) -> None:
    module = _load_consumer_main_module()
    _patch_debug_server(monkeypatch, module)

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
    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresMatchRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFilterEventRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "AccumulateMatchesUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "CrossEncoderReranker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "ProcessJobUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisQueueConsumer", lambda *_args, **_kwargs: queue_obj)
    monkeypatch.setattr(module.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "run_consumer", lambda *_args, **_kwargs: None)

    module.main()

    assert queue_obj.nack_all_inflight_calls == 1


def test_main_passes_postgres_pool_settings_to_repositories(monkeypatch, tmp_path: Path) -> None:
    module = _load_consumer_main_module()
    _patch_debug_server(monkeypatch, module)

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))
    monkeypatch.setenv("PG_POOL_MIN_CONNS", "0")
    monkeypatch.setenv("PG_POOL_MAX_CONNS", "1")
    monkeypatch.setenv("PG_STATEMENT_TIMEOUT_MS", "2500")

    repo_calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        module,
        "PostgresJobRepository",
        lambda *_args, **kwargs: repo_calls.append(("job", kwargs)) or object(),
    )
    monkeypatch.setattr(
        module,
        "PostgresUserRepository",
        lambda *_args, **kwargs: repo_calls.append(("user", kwargs)) or object(),
    )
    monkeypatch.setattr(
        module,
        "PostgresMatchRepository",
        lambda *_args, **kwargs: repo_calls.append(("match", kwargs)) or object(),
    )
    monkeypatch.setattr(
        module,
        "PostgresPendingJobsRepository",
        lambda *_args, **kwargs: repo_calls.append(("pending", kwargs)) or object(),
    )
    monkeypatch.setattr(
        module,
        "PostgresFeedbackRepository",
        lambda *_args, **kwargs: repo_calls.append(("feedback", kwargs)) or object(),
    )
    monkeypatch.setattr(
        module,
        "PostgresFilterEventRepository",
        lambda *_args, **kwargs: repo_calls.append(("filter_events", kwargs)) or object(),
    )
    monkeypatch.setattr(module, "AccumulateMatchesUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "CrossEncoderReranker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "ProcessJobUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisQueueConsumer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "run_consumer", lambda *_args, **_kwargs: None)

    module.main()

    assert repo_calls == [
        ("job", {"minconn": 0, "maxconn": 1, "statement_timeout_ms": 2500}),
        ("user", {"minconn": 0, "maxconn": 1, "statement_timeout_ms": 2500}),
        ("match", {"minconn": 0, "maxconn": 1, "statement_timeout_ms": 2500}),
        ("pending", {"minconn": 0, "maxconn": 1, "statement_timeout_ms": 2500}),
        ("feedback", {"minconn": 0, "maxconn": 1, "statement_timeout_ms": 2500}),
        ("filter_events", {"minconn": 0, "maxconn": 1, "statement_timeout_ms": 2500}),
    ]


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, True),
        ("0", False),
    ],
)
def test_main_passes_rerank_fallback_flag_into_process_job_use_case(
    monkeypatch,
    tmp_path: Path,
    env_value: str | None,
    expected: bool,
) -> None:
    module = _load_consumer_main_module()
    _patch_debug_server(monkeypatch, module)

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))
    if env_value is None:
        monkeypatch.delenv("RERANK_FALLBACK_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RERANK_FALLBACK_ENABLED", env_value)

    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresMatchRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFilterEventRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "AccumulateMatchesUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "SentenceTransformerEmbedding", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "CrossEncoderReranker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_warmup_embedding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "RedisQueueConsumer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "run_consumer", lambda *_args, **_kwargs: None)

    process_job_kwargs: list[dict] = []

    def fake_process_job_use_case(*_args, **kwargs):
        process_job_kwargs.append(kwargs)
        return object()

    monkeypatch.setattr(module, "ProcessJobUseCase", fake_process_job_use_case)

    module.main()

    assert len(process_job_kwargs) == 1
    assert process_job_kwargs[0]["rerank_fallback_enabled"] is expected
