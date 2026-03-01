"""Tests for ai-service cmd/ac_consumer/main.py helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


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
            self.claim_calls: list[tuple[int, int, int]] = []

        def list_unprocessed_user_ids(self, lease_timeout_sec: int):
            self.list_calls.append(lease_timeout_sec)
            return [10, 20, 30]

        def claim_unprocessed_job_ids(self, user_id: int, limit: int, lease_timeout_sec: int):
            self.claim_calls.append((user_id, limit, lease_timeout_sec))
            jobs_map = {
                10: [1],
                20: [2, 3],
                30: [],
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
    assert queue.messages[0].trace_id == ""
    assert pending_repo.list_calls == [600]
    assert pending_repo.claim_calls == [(10, 20, 600), (20, 20, 600), (30, 20, 600)]


def test_schedule_pending_batches_passes_custom_lease_timeout() -> None:
    module = _load_ac_consumer_main_module()

    class FakePendingRepo:
        def __init__(self) -> None:
            self.list_calls: list[int] = []
            self.claim_calls: list[tuple[int, int, int]] = []

        def list_unprocessed_user_ids(self, lease_timeout_sec: int):
            self.list_calls.append(lease_timeout_sec)
            return [20]

        def claim_unprocessed_job_ids(self, user_id: int, limit: int, lease_timeout_sec: int):
            self.claim_calls.append((user_id, limit, lease_timeout_sec))
            return [2, 3]

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
    assert queue.messages[0].trace_id == ""
    assert pending_repo.list_calls == [123]
    assert pending_repo.claim_calls == [(20, 20, 123)]


def test_ready_file_lifecycle(tmp_path: Path) -> None:
    module = _load_ac_consumer_main_module()
    ready_file = tmp_path / "ac-ready"

    module._mark_ready(str(ready_file))
    assert ready_file.exists()
    assert ready_file.read_text(encoding="utf-8") == "ready\n"

    module._cleanup_ready_file(str(ready_file))
    assert not ready_file.exists()
