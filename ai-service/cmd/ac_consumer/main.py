#!/usr/bin/env python3
"""Actor-Critic batch consumer entrypoint."""

from __future__ import annotations

import atexit
import inspect
import logging
import os
import signal
import sys
import threading
import time

# Add src to path for standalone run
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")  # when launched from ai-service/
except ImportError:
    pass  # python-dotenv is optional in container/runtime envs

from ai_service.adapter.actor import FallbackActorAgent, RuleBasedActorAgent
from ai_service.adapter.gemini import GeminiActorAgent
from ai_service.adapter.postgres import (
    PostgresFeedbackRepository,
    PostgresJobRepository,
    PostgresPendingJobsRepository,
    PostgresUserRepository,
)
from ai_service.adapter.redis import ACBatchMessage, RedisACBatchQueueConsumer, RedisMatchNotifyQueue
from ai_service.tracing.setup import extract_context, init_tracer
from ai_service.usecase.process_ac_batch import ACBatch, ProcessACBatchUseCase
from ai_service.util.fallback_metrics import increment_counter, set_gauge, start_metrics_server_from_env
from ai_service.util.postgres_pool_config import load_postgres_pool_settings
from ai_service.util.queue_retry import reclaim_with_retry, wait_before_retry
from ai_service.util.runtime_env import require_env, resolve_llm_provider, resolve_redis_url
from ai_service.util.shutdown import cap_blocking_pop_timeout
from ai_service.util.trace_context import reset_trace_id, set_trace_id
from ai_service.util.transport_security import (
    is_production_env,
    validate_postgres_tls_for_production,
    validate_redis_tls_for_production,
)

try:
    from opentelemetry import trace as otel_trace
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

READY_FILE_ENV = "AI_READY_FILE"
DEFAULT_READY_FILE = "/tmp/ai-ac-consumer-ready"
SHUTDOWN_GRACE_SEC_ENV = "AI_SHUTDOWN_GRACE_SEC"
DEFAULT_SHUTDOWN_GRACE_SEC = 20.0
PENDING_ROWS_METRIC_NAME = "ai_pending_ac_jobs_rows"
PENDING_ROWS_METRIC_HELP = "Current number of rows in pending_ac_jobs grouped by state."
PENDING_CLEANUP_TOTAL_METRIC_NAME = "ai_pending_ac_jobs_cleanup_total"
PENDING_CLEANUP_TOTAL_METRIC_HELP = "Total number of processed pending_ac_jobs rows deleted by retention cleanup."
PENDING_RETENTION_DAYS_ENV = "AC_PENDING_RETENTION_DAYS"
DEFAULT_PENDING_RETENTION_DAYS = 14
PENDING_CLEANUP_INTERVAL_SEC_ENV = "AC_PENDING_CLEANUP_INTERVAL_SEC"
DEFAULT_PENDING_CLEANUP_INTERVAL_SEC = 3600
PENDING_METRICS_REFRESH_SEC_ENV = "AC_PENDING_METRICS_REFRESH_SEC"
DEFAULT_PENDING_METRICS_REFRESH_SEC = 30
POP_TIMEOUT_SEC_ENV = "AI_AC_BATCH_POP_TIMEOUT_SEC"
DEFAULT_POP_TIMEOUT_SEC = 30
RERANK_FALLBACK_ENABLED_ENV = "RERANK_FALLBACK_ENABLED"
DEFAULT_RERANK_FALLBACK_ENABLED = True


def _cleanup_ready_file(ready_file: str) -> None:
    try:
        os.remove(ready_file)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("failed to remove ready file %s: %s", ready_file, exc)


def _mark_ready(ready_file: str) -> None:
    try:
        with open(ready_file, "w", encoding="utf-8") as file:
            file.write("ready\n")
    except OSError as exc:
        logger.error("failed to write ready file %s: %s", ready_file, exc)
        sys.exit(1)


def _schedule_pending_batches(
    *,
    pending_repo: PostgresPendingJobsRepository,
    queue: RedisACBatchQueueConsumer,
    min_jobs: int,
    max_jobs: int,
    lease_timeout_sec: int,
) -> int:
    scheduled = 0
    user_ids = pending_repo.list_unprocessed_user_ids(lease_timeout_sec=lease_timeout_sec)
    for user_id in user_ids:
        job_ids, trace_id = pending_repo.claim_unprocessed_job_ids_with_trace(
            user_id,
            limit=max_jobs,
            lease_timeout_sec=lease_timeout_sec,
            min_jobs=min_jobs,
        )
        if len(job_ids) < min_jobs:
            continue
        queue.enqueue(ACBatchMessage(user_id=user_id, job_ids=job_ids, trace_id=trace_id))
        scheduled += 1
    if scheduled > 0:
        logger.info("scheduled %d ac batches", scheduled)
    return scheduled


def _shutdown_grace_sec() -> float:
    raw = os.getenv(SHUTDOWN_GRACE_SEC_ENV, str(DEFAULT_SHUTDOWN_GRACE_SEC))
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "invalid %s=%r, fallback to %.1fs",
            SHUTDOWN_GRACE_SEC_ENV,
            raw,
            DEFAULT_SHUTDOWN_GRACE_SEC,
        )
        return DEFAULT_SHUTDOWN_GRACE_SEC
    if value <= 0:
        logger.warning(
            "non-positive %s=%r, fallback to %.1fs",
            SHUTDOWN_GRACE_SEC_ENV,
            raw,
            DEFAULT_SHUTDOWN_GRACE_SEC,
        )
        return DEFAULT_SHUTDOWN_GRACE_SEC
    return value


def _nack_inflight_messages(queue: object) -> int:
    nack_all_inflight = getattr(queue, "nack_all_inflight", None)
    if not callable(nack_all_inflight):
        return 0
    try:
        return int(nack_all_inflight())
    except Exception as exc:  # noqa: BLE001
        logger.exception("nack_all_inflight failed: %s", exc)
        return 0


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        logger.warning("invalid %s=%r, fallback to %d", name, raw, default)
        return default
    if value <= 0:
        logger.warning("non-positive %s=%r, fallback to %d", name, raw, default)
        return default
    return value


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _supports_constructor_kwarg(factory: object, name: str) -> bool:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return True

    if name in signature.parameters:
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _observe_pending_job_table_metrics(pending_repo: PostgresPendingJobsRepository) -> None:
    total_rows = int(pending_repo.count_rows())
    unprocessed_rows = int(pending_repo.count_unprocessed_rows())
    set_gauge(
        PENDING_ROWS_METRIC_NAME,
        PENDING_ROWS_METRIC_HELP,
        total_rows,
        labels={"state": "total"},
    )
    set_gauge(
        PENDING_ROWS_METRIC_NAME,
        PENDING_ROWS_METRIC_HELP,
        unprocessed_rows,
        labels={"state": "unprocessed"},
    )


def _cleanup_old_pending_rows(
    pending_repo: PostgresPendingJobsRepository,
    *,
    older_than_days: int,
) -> int:
    deleted = int(pending_repo.delete_processed_older_than(older_than_days))
    if deleted > 0:
        increment_counter(
            PENDING_CLEANUP_TOTAL_METRIC_NAME,
            PENDING_CLEANUP_TOTAL_METRIC_HELP,
            deleted,
        )
        logger.info(
            "deleted %d processed pending_ac_jobs rows older than %d days",
            deleted,
            older_than_days,
        )
    return deleted


def main() -> None:
    ready_file = os.getenv(READY_FILE_ENV, DEFAULT_READY_FILE)
    _cleanup_ready_file(ready_file)
    atexit.register(_cleanup_ready_file, ready_file)

    app_env = os.getenv("APP_ENV", "development")
    try:
        db_url = require_env("DATABASE_URL", os.getenv("DATABASE_URL"))
        redis_url = resolve_redis_url(app_env, os.getenv("REDIS_URL"))
        _llm_provider = resolve_llm_provider(os.getenv("LLM_PROVIDER"))
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    if is_production_env(app_env):
        try:
            validate_postgres_tls_for_production("DATABASE_URL", db_url)
            validate_redis_tls_for_production("REDIS_URL", redis_url)
        except ValueError as exc:
            logger.error("%s", exc)
            sys.exit(1)
    try:
        pg_pool_kwargs = load_postgres_pool_settings()
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    start_metrics_server_from_env(
        port_env="AI_AC_CONSUMER_METRICS_PORT",
        default_port=0,
    )

    ac_batch_queue_name = os.getenv("AC_BATCH_QUEUE", "ac-batch")
    batch_interval = int(os.getenv("AC_BATCH_INTERVAL_SEC", "300"))
    batch_min_jobs = int(os.getenv("AC_BATCH_MIN_JOBS", "1"))
    batch_max_jobs = int(os.getenv("AC_BATCH_MAX_JOBS", "20"))
    lease_timeout_sec = int(os.getenv("AC_LEASE_TIMEOUT_SEC", "600"))
    pending_retention_days = _read_positive_int_env(PENDING_RETENTION_DAYS_ENV, DEFAULT_PENDING_RETENTION_DAYS)
    pending_cleanup_interval_sec = _read_positive_int_env(
        PENDING_CLEANUP_INTERVAL_SEC_ENV,
        DEFAULT_PENDING_CLEANUP_INTERVAL_SEC,
    )
    pending_metrics_refresh_sec = _read_positive_int_env(
        PENDING_METRICS_REFRESH_SEC_ENV,
        DEFAULT_PENDING_METRICS_REFRESH_SEC,
    )
    grace_sec = _shutdown_grace_sec()
    pop_timeout_sec = cap_blocking_pop_timeout(
        _read_positive_int_env(POP_TIMEOUT_SEC_ENV, DEFAULT_POP_TIMEOUT_SEC),
        grace_sec,
        logger=logger,
        timeout_name=POP_TIMEOUT_SEC_ENV,
        grace_name=SHUTDOWN_GRACE_SEC_ENV,
    )

    rerank_threshold = float(os.getenv("RERANK_THRESHOLD", "0.55"))
    rerank_fallback_enabled = _read_bool_env(
        RERANK_FALLBACK_ENABLED_ENV,
        DEFAULT_RERANK_FALLBACK_ENABLED,
    )
    max_jobs_to_send = 5

    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_api_key:
        logger.error("GEMINI_API_KEY not set (required when LLM_PROVIDER=gemini)")
        sys.exit(1)
    actor_model = os.getenv("GEMINI_ACTOR_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
    actor_timeout = int(os.getenv("ACTOR_GEMINI_TIMEOUT_SEC", "30"))
    actor_primary = GeminiActorAgent(api_key=gemini_api_key, model=actor_model, timeout_sec=actor_timeout)
    logger.info("actor provider=gemini model_actor=%s", actor_model)

    init_tracer("site-parser-ac")

    user_repo = PostgresUserRepository(db_url, **pg_pool_kwargs)
    job_repo = PostgresJobRepository(db_url, **pg_pool_kwargs)
    pending_repo = PostgresPendingJobsRepository(db_url, **pg_pool_kwargs)
    feedback_repo = PostgresFeedbackRepository(db_url, **pg_pool_kwargs)
    notify_queue = RedisMatchNotifyQueue(redis_url)
    stop_event = threading.Event()
    ac_batch_queue = RedisACBatchQueueConsumer(redis_url, queue_name=ac_batch_queue_name)
    if not reclaim_with_retry(
        reclaim=ac_batch_queue.reclaim_stuck,
        stop_event=stop_event,
        logger=logger,
        operation="ac batch reclaim",
    ):
        logger.info("ac consumer stopped before reclaim completed")
        return

    actor = FallbackActorAgent(
        primary=actor_primary,
        fallback=RuleBasedActorAgent(),
    )
    process_batch_kwargs = {
        "user_repo": user_repo,
        "job_repo": job_repo,
        "pending_repo": pending_repo,
        "actor": actor,
        "notify_queue": notify_queue,
        "feedback_repo": feedback_repo,
        "rerank_threshold": rerank_threshold,
        "max_jobs_to_send": max_jobs_to_send,
    }
    if _supports_constructor_kwarg(ProcessACBatchUseCase, "rerank_fallback_enabled"):
        process_batch_kwargs["rerank_fallback_enabled"] = rerank_fallback_enabled
    process_batch = ProcessACBatchUseCase(**process_batch_kwargs)
    _mark_ready(ready_file)

    consumer_stopped = threading.Event()
    force_exit_lock = threading.Lock()
    force_exit_started = False

    def force_exit(exit_code: int) -> None:
        requeued = _nack_inflight_messages(ac_batch_queue)
        if requeued > 0:
            logger.warning("requeued %d in-flight ac batches before forced shutdown", requeued)
        os._exit(exit_code)

    def force_exit_on_timeout() -> None:
        if consumer_stopped.wait(timeout=grace_sec):
            return
        logger.error(
            "graceful shutdown timed out after %.1fs; forcing process exit",
            grace_sec,
        )
        force_exit(1)

    def on_signal(signum: int, frame: object) -> None:
        nonlocal force_exit_started
        _ = frame
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        logger.info("shutdown signal received: %s", signal_name)
        stop_event.set()
        with force_exit_lock:
            if not force_exit_started:
                force_exit_started = True
                t = threading.Thread(
                    target=force_exit_on_timeout,
                    daemon=True,
                    name="ac-consumer-force-exit",
                )
                t.start()
                return
        logger.warning("second shutdown signal received, forcing immediate exit")
        force_exit(1)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    logger.info("ac consumer started, queue=%s pop_timeout_sec=%d", ac_batch_queue_name, pop_timeout_sec)
    next_schedule_at = 0.0
    next_pending_metrics_at = 0.0
    next_pending_cleanup_at = 0.0
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            if now >= next_pending_metrics_at:
                try:
                    _observe_pending_job_table_metrics(pending_repo)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("failed to refresh pending_ac_jobs metrics: %s", exc)
                next_pending_metrics_at = now + max(1, pending_metrics_refresh_sec)
            if now >= next_pending_cleanup_at:
                try:
                    _cleanup_old_pending_rows(pending_repo, older_than_days=pending_retention_days)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("failed to cleanup old pending_ac_jobs rows: %s", exc)
                next_pending_cleanup_at = now + max(1, pending_cleanup_interval_sec)
            if now >= next_schedule_at:
                try:
                    _schedule_pending_batches(
                        pending_repo=pending_repo,
                        queue=ac_batch_queue,
                        min_jobs=batch_min_jobs,
                        max_jobs=batch_max_jobs,
                        lease_timeout_sec=lease_timeout_sec,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("failed to schedule ac batches: %s", exc)
                next_schedule_at = now + max(1, batch_interval)

            try:
                message = ac_batch_queue.pop_blocking(timeout_sec=pop_timeout_sec)
            except Exception as exc:  # noqa: BLE001
                if wait_before_retry(
                    stop_event=stop_event,
                    logger=logger,
                    operation="ac batch queue pop",
                    exc=exc,
                ):
                    break
                continue
            if message is None:
                continue
            if stop_event.is_set():
                try:
                    ac_batch_queue.nack(message.user_id)
                except Exception as nack_exc:  # noqa: BLE001
                    logger.exception("shutdown ac batch nack failed user_id=%d: %s", message.user_id, nack_exc)
                break
            token = set_trace_id(message.trace_id)
            trace_ctx = extract_context(message.traceparent)
            try:
                if _OTEL_AVAILABLE:
                    tracer = otel_trace.get_tracer(__name__)
                    with tracer.start_as_current_span("ac.process_batch", context=trace_ctx) as span:  # type: ignore[arg-type]
                        span.set_attribute("user.id", message.user_id)
                        span.set_attribute("batch.size", len(message.job_ids))
                        process_batch.execute(ACBatch(user_id=message.user_id, job_ids=message.job_ids))
                else:
                    process_batch.execute(ACBatch(user_id=message.user_id, job_ids=message.job_ids))
            except Exception as exc:  # noqa: BLE001
                logger.exception("ac batch processing failed user_id=%d: %s", message.user_id, exc)
                try:
                    ac_batch_queue.nack(message.user_id)
                except Exception as nack_exc:  # noqa: BLE001
                    logger.exception("ac batch nack failed user_id=%d: %s", message.user_id, nack_exc)
            else:
                try:
                    ac_batch_queue.ack(message.user_id)
                except Exception as ack_exc:  # noqa: BLE001
                    logger.exception("ac batch ack failed user_id=%d: %s", message.user_id, ack_exc)
            finally:
                reset_trace_id(token)
    finally:
        requeued = _nack_inflight_messages(ac_batch_queue)
        if requeued > 0:
            logger.warning("requeued %d in-flight ac batches during shutdown", requeued)
        consumer_stopped.set()
    logger.info("ac consumer stopped")


if __name__ == "__main__":
    main()
