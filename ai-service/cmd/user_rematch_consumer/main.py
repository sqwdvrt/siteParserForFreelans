#!/usr/bin/env python3
"""AI Service user-rematch consumer: BRPOP user-rematch → ProcessUserRematch. Точка входа."""

import atexit
import logging
import os
import signal
import sys
import threading

# Add src to path for standalone run
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")  # при запуске из ai-service/
except ImportError:
    pass  # python-dotenv не установлен — используй переменные из окружения

from ai_service.adapter.postgres import PostgresFeedbackRepository, PostgresMatchRepository, PostgresUserRepository
from ai_service.adapter.redis import RedisMatchNotifyQueue
from ai_service.adapter.redis.user_rematch_queue import RedisUserRematchQueueConsumer
from ai_service.util.postgres_pool_config import load_postgres_pool_settings
from ai_service.util.transport_security import (
    is_production_env,
    validate_postgres_tls_for_production,
    validate_redis_tls_for_production,
)
from ai_service.usecase.process_user_rematch import ProcessUserRematchUseCase
from ai_service.util.health_server import start_health_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

READY_FILE_ENV = "AI_READY_FILE"
DEFAULT_READY_FILE = "/tmp/ai-user-rematch-ready"
SHUTDOWN_GRACE_SEC_ENV = "AI_SHUTDOWN_GRACE_SEC"
DEFAULT_SHUTDOWN_GRACE_SEC = 20.0


def _cleanup_ready_file(ready_file: str) -> None:
    try:
        os.remove(ready_file)
    except FileNotFoundError:
        return
    except OSError as e:
        logger.warning("failed to remove ready file %s: %s", ready_file, e)


def _mark_ready(ready_file: str) -> None:
    try:
        with open(ready_file, "w", encoding="utf-8") as f:
            f.write("ready\n")
    except OSError as e:
        logger.error("failed to write ready file %s: %s", ready_file, e)
        sys.exit(1)


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
    except Exception as e:
        logger.exception("nack_all_inflight failed: %s", e)
        return 0


def _maybe_shutdown_requeue(queue: RedisUserRematchQueueConsumer, user_id: int, stop: threading.Event) -> bool:
    if not stop.is_set():
        return False
    nack = getattr(queue, "nack", None)
    if callable(nack):
        try:
            nack(user_id)
        except Exception as nack_err:
            logger.exception("shutdown nack user_id=%s failed: %s", user_id, nack_err)
    return True


def _run_consumer(
    queue: RedisUserRematchQueueConsumer,
    process_user_rematch: ProcessUserRematchUseCase,
    *,
    timeout_sec: int = 5,
    stop_event: threading.Event,
) -> None:
    """Цикл: BRPOP user-rematch → ProcessUserRematch. Выход по stop_event.set()."""
    reclaim = getattr(queue, "reclaim_stuck", None)
    if callable(reclaim):
        reclaim()

    while not stop_event.is_set():
        try:
            user_id = queue.pop_blocking(timeout_sec=timeout_sec)
        except Exception as e:
            logger.exception("user-rematch queue pop failed: %s", e)
            continue
        if user_id is not None:
            if _maybe_shutdown_requeue(queue, user_id, stop_event):
                break
            try:
                process_user_rematch.execute(user_id)
            except Exception as e:
                logger.exception("rematch user_id=%s failed: %s", user_id, e)
                nack = getattr(queue, "nack", None)
                if callable(nack):
                    try:
                        nack(user_id)
                    except Exception as nack_err:
                        logger.exception("nack user_id=%s failed: %s", user_id, nack_err)
            else:
                ack = getattr(queue, "ack", None)
                if callable(ack):
                    try:
                        ack(user_id)
                    except Exception as ack_err:
                        logger.exception("ack user_id=%s failed: %s", user_id, ack_err)
    logger.info("user-rematch consumer loop stopped")


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
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(1)
    try:
        pg_pool_kwargs = load_postgres_pool_settings()
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)

    days_back = int(os.getenv("REMATCH_JOBS_DAYS_BACK", "7"))
    max_jobs = int(os.getenv("REMATCH_MAX_JOBS", "5"))
    threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))

    user_repo = PostgresUserRepository(db_url, **pg_pool_kwargs)
    match_repo = PostgresMatchRepository(db_url, **pg_pool_kwargs)
    feedback_repo = PostgresFeedbackRepository(db_url, **pg_pool_kwargs)
    match_notify_queue = RedisMatchNotifyQueue(redis_url)
    process_user_rematch = ProcessUserRematchUseCase(
        user_repo,
        match_repo,
        match_notify_queue,
        similarity_threshold=threshold,
        max_jobs=max_jobs,
        days_back=days_back,
        feedback_repo=feedback_repo,
    )
    queue = RedisUserRematchQueueConsumer(redis_url)
    health_port = int(os.getenv("AI_HEALTH_PORT", "8092"))
    start_health_server(health_port)
    _mark_ready(ready_file)

    stop_event = threading.Event()
    consumer_stopped = threading.Event()
    grace_sec = _shutdown_grace_sec()
    force_exit_lock = threading.Lock()
    force_exit_started = False

    def force_exit(exit_code: int) -> None:
        requeued = _nack_inflight_messages(queue)
        if requeued > 0:
            logger.warning("requeued %d in-flight user-rematch messages before forced shutdown", requeued)
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
                    name="user-rematch-consumer-force-exit",
                )
                t.start()
                return
        logger.warning("second shutdown signal received, forcing immediate exit")
        force_exit(1)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    logger.info("user-rematch consumer started")
    try:
        _run_consumer(queue, process_user_rematch, timeout_sec=5, stop_event=stop_event)
    finally:
        requeued = _nack_inflight_messages(queue)
        if requeued > 0:
            logger.warning("requeued %d in-flight user-rematch messages during shutdown", requeued)
        consumer_stopped.set()
    logger.info("user-rematch consumer stopped")


if __name__ == "__main__":
    main()
