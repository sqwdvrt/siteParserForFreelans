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

    days_back = int(os.getenv("REMATCH_JOBS_DAYS_BACK", "7"))
    max_jobs = int(os.getenv("REMATCH_MAX_JOBS", "5"))
    threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))

    user_repo = PostgresUserRepository(db_url)
    match_repo = PostgresMatchRepository(db_url)
    feedback_repo = PostgresFeedbackRepository(db_url)
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

    def on_signal(signum: int, frame: object) -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    logger.info("user-rematch consumer started")
    _run_consumer(queue, process_user_rematch, timeout_sec=5, stop_event=stop_event)
    logger.info("user-rematch consumer stopped")


if __name__ == "__main__":
    main()
