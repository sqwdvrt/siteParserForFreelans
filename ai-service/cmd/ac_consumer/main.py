#!/usr/bin/env python3
"""Actor-Critic batch consumer entrypoint."""

from __future__ import annotations

import atexit
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

from ai_service.adapter.actor import FallbackActorAgent, OllamaActorAgent, RuleBasedActorAgent
from ai_service.adapter.critic import FallbackCriticAgent, OllamaCriticAgent, RuleBasedCriticAgent
from ai_service.adapter.postgres import (
    PostgresJobRepository,
    PostgresPendingJobsRepository,
    PostgresUserRepository,
)
from ai_service.adapter.redis import ACBatchMessage, RedisACBatchQueueConsumer, RedisMatchNotifyQueue
from ai_service.tracing.setup import extract_context, init_tracer
from ai_service.usecase.actor_critic_loop import ActorCriticConfig, ActorCriticLoop
from ai_service.usecase.process_ac_batch import ACBatch, ProcessACBatchUseCase
from ai_service.util.fallback_metrics import start_metrics_server_from_env
from ai_service.util.ollama_probe import probe_ollama
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


def main() -> None:
    ready_file = os.getenv(READY_FILE_ENV, DEFAULT_READY_FILE)
    _cleanup_ready_file(ready_file)
    atexit.register(_cleanup_ready_file, ready_file)

    app_env = os.getenv("APP_ENV", "development")
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    if is_production_env(app_env):
        try:
            validate_postgres_tls_for_production("DATABASE_URL", db_url)
            validate_redis_tls_for_production("REDIS_URL", redis_url)
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

    score_threshold = float(os.getenv("AC_SCORE_THRESHOLD", "5.0"))
    max_attempts = int(os.getenv("AC_MAX_ATTEMPTS", "3"))
    max_jobs_per_selection = int(os.getenv("AC_MAX_JOBS_PER_SELECTION", "5"))

    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434")
    actor_model = os.getenv("ACTOR_OLLAMA_MODEL", "llama3.2:3b-instruct-q4_K_M")
    critic_model = os.getenv("CRITIC_OLLAMA_MODEL", "llama3.2:3b-instruct-q4_K_M")
    actor_timeout = int(os.getenv("ACTOR_OLLAMA_TIMEOUT_SEC", "45"))
    critic_timeout = int(os.getenv("CRITIC_OLLAMA_TIMEOUT_SEC", "30"))
    ollama_required = os.getenv("OLLAMA_REQUIRED", "1" if is_production_env(app_env) else "0") == "1"
    if not probe_ollama(
        ollama_url,
        required=ollama_required,
        required_models=[actor_model, critic_model],
    ) and ollama_required:
        sys.exit(1)

    init_tracer("site-parser-ac")

    user_repo = PostgresUserRepository(db_url)
    job_repo = PostgresJobRepository(db_url)
    pending_repo = PostgresPendingJobsRepository(db_url)
    notify_queue = RedisMatchNotifyQueue(redis_url)
    ac_batch_queue = RedisACBatchQueueConsumer(redis_url, queue_name=ac_batch_queue_name)
    ac_batch_queue.reclaim_stuck()

    actor = FallbackActorAgent(
        primary=OllamaActorAgent(base_url=ollama_url, model=actor_model, timeout_sec=actor_timeout),
        fallback=RuleBasedActorAgent(),
    )
    critic = FallbackCriticAgent(
        primary=OllamaCriticAgent(base_url=ollama_url, model=critic_model, timeout_sec=critic_timeout),
        fallback=RuleBasedCriticAgent(),
    )
    ac_loop = ActorCriticLoop(
        actor=actor,
        critic=critic,
        config=ActorCriticConfig(
            score_threshold=score_threshold,
            max_attempts=max_attempts,
            max_jobs_per_selection=max_jobs_per_selection,
        ),
    )
    process_batch = ProcessACBatchUseCase(
        user_repo=user_repo,
        job_repo=job_repo,
        pending_repo=pending_repo,
        ac_loop=ac_loop,
        notify_queue=notify_queue,
    )
    _mark_ready(ready_file)

    stop_event = threading.Event()
    consumer_stopped = threading.Event()
    grace_sec = _shutdown_grace_sec()
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

    logger.info("ac consumer started, queue=%s", ac_batch_queue_name)
    next_schedule_at = 0.0
    try:
        while not stop_event.is_set():
            now = time.monotonic()
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
                message = ac_batch_queue.pop_blocking(timeout_sec=1)
            except Exception as exc:  # noqa: BLE001
                logger.exception("ac batch queue pop failed: %s", exc)
                continue
            if message is None:
                continue
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
        consumer_stopped.set()
    logger.info("ac consumer stopped")


if __name__ == "__main__":
    main()
