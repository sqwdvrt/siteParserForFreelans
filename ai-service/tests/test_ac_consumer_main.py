"""Tests for ai-service cmd/ac_consumer/main.py helpers."""

from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path

import pytest

from ai_service.util import fallback_metrics


def _load_ac_consumer_main_module():
    module_path = Path(__file__).resolve().parents[1] / "cmd" / "ac_consumer" / "main.py"
    spec = importlib.util.spec_from_file_location("ac_consumer_main_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schedule_pending_batches_respects_min_jobs() -> None:
    module = _load_ac_consumer_main_module()

    class FakePendingRepo:
        def __init__(self) -> None:
            self.list_calls: list[int] = []
            self.claim_calls: list[tuple[int, int, int, int]] = []

        def list_unprocessed_user_ids(self, lease_timeout_sec: int):
            self.list_calls.append(lease_timeout_sec)
            return [10, 20, 30]

        def claim_unprocessed_job_ids_with_trace(
            self,
            user_id: int,
            limit: int,
            lease_timeout_sec: int,
            min_jobs: int,
        ):
            self.claim_calls.append((user_id, limit, lease_timeout_sec, min_jobs))
            jobs_map = {
                10: ([1], "trace-10"),
                20: ([2, 3], "trace-20"),
                30: ([], "trace-30"),
            }
            return jobs_map[user_id]

    class FakeQueue:
        def __init__(self) -> None:
            self.messages = []

        def enqueue(self, msg) -> None:
            self.messages.append(msg)

    pending_repo = FakePendingRepo()
    queue = FakeQueue()
    scheduled = module._schedule_pending_batches(
        pending_repo=pending_repo,
        queue=queue,
        min_jobs=2,
        max_jobs=20,
        lease_timeout_sec=600,
    )

    assert scheduled == 1
    assert len(queue.messages) == 1
    assert queue.messages[0].user_id == 20
    assert queue.messages[0].job_ids == [2, 3]
    assert queue.messages[0].trace_id == "trace-20"
    assert pending_repo.list_calls == [600]
    assert pending_repo.claim_calls == [(10, 20, 600, 2), (20, 20, 600, 2), (30, 20, 600, 2)]


def test_observe_pending_job_table_metrics_exports_total_and_unprocessed() -> None:
    module = _load_ac_consumer_main_module()
    fallback_metrics.reset_counters_for_tests()

    class FakePendingRepo:
        def count_rows(self) -> int:
            return 13

        def count_unprocessed_rows(self) -> int:
            return 5

    module._observe_pending_job_table_metrics(FakePendingRepo())

    rendered = fallback_metrics.render_prometheus_text()
    assert 'ai_pending_ac_jobs_rows{state="total"} 13.0' in rendered
    assert 'ai_pending_ac_jobs_rows{state="unprocessed"} 5.0' in rendered


def test_cleanup_old_pending_rows_increments_cleanup_counter() -> None:
    module = _load_ac_consumer_main_module()
    fallback_metrics.reset_counters_for_tests()

    class FakePendingRepo:
        def delete_processed_older_than(self, older_than_days: int) -> int:
            assert older_than_days == 14
            return 4

    deleted = module._cleanup_old_pending_rows(FakePendingRepo(), older_than_days=14)

    assert deleted == 4
    rendered = fallback_metrics.render_prometheus_text()
    assert "ai_pending_ac_jobs_cleanup_total 4.0" in rendered


def test_main_exits_on_invalid_llm_provider(monkeypatch, tmp_path: Path) -> None:
    module = _load_ac_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))

    with pytest.raises(SystemExit):
        module.main()


def test_main_exits_when_llm_provider_missing(monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    module = _load_ac_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exc_info:
            module.main()

    assert exc_info.value.code == 1
    assert "LLM_PROVIDER not set" in caplog.text


def test_schedule_pending_batches_passes_custom_lease_timeout() -> None:
    module = _load_ac_consumer_main_module()

    class FakePendingRepo:
        def __init__(self) -> None:
            self.list_calls: list[int] = []
            self.claim_calls: list[tuple[int, int, int, int]] = []

        def list_unprocessed_user_ids(self, lease_timeout_sec: int):
            self.list_calls.append(lease_timeout_sec)
            return [20]

        def claim_unprocessed_job_ids_with_trace(
            self,
            user_id: int,
            limit: int,
            lease_timeout_sec: int,
            min_jobs: int,
        ):
            self.claim_calls.append((user_id, limit, lease_timeout_sec, min_jobs))
            return [2, 3], "trace-custom"

    class FakeQueue:
        def __init__(self) -> None:
            self.messages = []

        def enqueue(self, msg) -> None:
            self.messages.append(msg)

    pending_repo = FakePendingRepo()
    queue = FakeQueue()
    scheduled = module._schedule_pending_batches(
        pending_repo=pending_repo,
        queue=queue,
        min_jobs=1,
        max_jobs=20,
        lease_timeout_sec=123,
    )

    assert scheduled == 1
    assert len(queue.messages) == 1
    assert queue.messages[0].user_id == 20
    assert queue.messages[0].job_ids == [2, 3]
    assert queue.messages[0].trace_id == "trace-custom"
    assert pending_repo.list_calls == [123]
    assert pending_repo.claim_calls == [(20, 20, 123, 1)]


def test_ready_file_lifecycle(tmp_path: Path) -> None:
    module = _load_ac_consumer_main_module()
    ready_file = tmp_path / "ac-ready"

    module._mark_ready(str(ready_file))
    assert ready_file.exists()
    assert ready_file.read_text(encoding="utf-8") == "ready\n"

    module._cleanup_ready_file(str(ready_file))
    assert not ready_file.exists()


def test_shutdown_grace_sec_invalid_env_fallback(monkeypatch) -> None:
    module = _load_ac_consumer_main_module()
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "bad-value")

    assert module._shutdown_grace_sec() == module.DEFAULT_SHUTDOWN_GRACE_SEC


def test_main_caps_pop_timeout_to_shutdown_grace(monkeypatch, tmp_path: Path) -> None:
    module = _load_ac_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "20")
    monkeypatch.setenv(module.POP_TIMEOUT_SEC_ENV, "30")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))
    monkeypatch.setenv("AC_BATCH_INTERVAL_SEC", "3600")

    monkeypatch.setattr(module, "start_metrics_server_from_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisMatchNotifyQueue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisACBatchQueueConsumer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "GeminiActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RuleBasedActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "FallbackActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_schedule_pending_batches", lambda **_kwargs: 0)
    monkeypatch.setattr(module, "ProcessACBatchUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module.signal, "signal", lambda *_args, **_kwargs: None)

    seen_timeout: list[int] = []

    class StopAfterOnePopQueue:
        def reclaim_stuck(self) -> None:
            return None

        def pop_blocking(self, timeout_sec: int = 1):
            seen_timeout.append(timeout_sec)
            raise SystemExit

        def nack_all_inflight(self) -> int:
            return 0

    monkeypatch.setattr(module, "RedisACBatchQueueConsumer", lambda *_args, **_kwargs: StopAfterOnePopQueue())

    with pytest.raises(SystemExit):
        module.main()

    assert seen_timeout == [19]


def test_main_forces_requeue_on_shutdown_timeout(monkeypatch, tmp_path: Path) -> None:
    module = _load_ac_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv(module.SHUTDOWN_GRACE_SEC_ENV, "0.05")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))
    monkeypatch.setenv("AC_BATCH_INTERVAL_SEC", "3600")

    class FakeQueue:
        def __init__(self) -> None:
            self.nack_all_inflight_calls = 0
            self._popped = False

        def reclaim_stuck(self) -> None:
            return None

        def pop_blocking(self, timeout_sec: int = 1):
            _ = timeout_sec
            if self._popped:
                return None
            self._popped = True
            return module.ACBatchMessage(user_id=10, job_ids=[1, 2], trace_id="trace-ac-1")

        def ack(self, user_id: int) -> None:
            _ = user_id

        def nack(self, user_id: int) -> None:
            _ = user_id

        def nack_all_inflight(self) -> int:
            self.nack_all_inflight_calls += 1
            return 1

    queue_obj = FakeQueue()
    monkeypatch.setattr(module, "start_metrics_server_from_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisMatchNotifyQueue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisACBatchQueueConsumer", lambda *_args, **_kwargs: queue_obj)
    monkeypatch.setattr(module, "GeminiActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RuleBasedActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "FallbackActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_schedule_pending_batches", lambda **_kwargs: 0)

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

    class FakeProcessBatch:
        def execute(self, _batch) -> None:
            handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
            assert force_exit_event.wait(timeout=1.0)

    monkeypatch.setattr(module, "ProcessACBatchUseCase", lambda *_args, **_kwargs: FakeProcessBatch())

    module.main()

    assert forced_exit_codes == [1]
    assert queue_obj.nack_all_inflight_calls == 2


def test_main_requeues_batch_if_shutdown_happens_after_pop(monkeypatch, tmp_path: Path) -> None:
    module = _load_ac_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))
    monkeypatch.setenv("AC_BATCH_INTERVAL_SEC", "3600")

    handlers: dict[int, object] = {}

    def fake_signal(sig, handler):
        handlers[sig] = handler

    class FakeQueue:
        def __init__(self) -> None:
            self.pop_calls = 0
            self.nacked: list[int] = []
            self.nack_all_inflight_calls = 0

        def reclaim_stuck(self) -> None:
            return None

        def pop_blocking(self, timeout_sec: int = 1):
            _ = timeout_sec
            if self.pop_calls > 0:
                return None
            self.pop_calls += 1
            handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
            return module.ACBatchMessage(user_id=10, job_ids=[1, 2], trace_id="trace-ac-1")

        def ack(self, user_id: int) -> None:
            raise AssertionError(f"unexpected ack for user_id={user_id}")

        def nack(self, user_id: int) -> None:
            self.nacked.append(user_id)

        def nack_all_inflight(self) -> int:
            self.nack_all_inflight_calls += 1
            return 0

    queue_obj = FakeQueue()
    process_calls: list[object] = []

    class FakeProcessBatch:
        def execute(self, batch) -> None:
            process_calls.append(batch)

    monkeypatch.setattr(module, "start_metrics_server_from_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisMatchNotifyQueue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisACBatchQueueConsumer", lambda *_args, **_kwargs: queue_obj)
    monkeypatch.setattr(module, "GeminiActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RuleBasedActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "FallbackActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_schedule_pending_batches", lambda **_kwargs: 0)
    monkeypatch.setattr(module, "ProcessACBatchUseCase", lambda *_args, **_kwargs: FakeProcessBatch())
    monkeypatch.setattr(module.signal, "signal", fake_signal)

    module.main()

    assert queue_obj.nacked == [10]
    assert process_calls == []
    assert queue_obj.nack_all_inflight_calls == 1


def test_main_retries_reclaim_error(monkeypatch, tmp_path: Path) -> None:
    module = _load_ac_consumer_main_module()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("AI_QUEUE_RETRY_DELAY_SEC", "0")
    monkeypatch.setenv(module.READY_FILE_ENV, str(tmp_path / "ready"))
    monkeypatch.setenv("AC_BATCH_INTERVAL_SEC", "3600")

    handlers: dict[int, object] = {}

    def fake_signal(sig, handler):
        handlers[sig] = handler

    class FakeQueue:
        def __init__(self) -> None:
            self.reclaim_calls = 0

        def reclaim_stuck(self) -> None:
            self.reclaim_calls += 1
            if self.reclaim_calls == 1:
                raise RuntimeError("redis temporary error")

        def pop_blocking(self, timeout_sec: int = 1):
            _ = timeout_sec
            handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
            return None

        def nack_all_inflight(self) -> int:
            return 0

    queue_obj = FakeQueue()
    monkeypatch.setattr(module, "start_metrics_server_from_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "PostgresUserRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresJobRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresPendingJobsRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "PostgresFeedbackRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisMatchNotifyQueue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RedisACBatchQueueConsumer", lambda *_args, **_kwargs: queue_obj)
    monkeypatch.setattr(module, "GeminiActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "RuleBasedActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "FallbackActorAgent", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_schedule_pending_batches", lambda **_kwargs: 0)
    monkeypatch.setattr(module, "ProcessACBatchUseCase", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module.signal, "signal", fake_signal)

    module.main()

    assert queue_obj.reclaim_calls == 2
