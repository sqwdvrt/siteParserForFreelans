#!/usr/bin/env python3
"""AI Service consumer: BRPOP → ProcessJob. Точка входа."""

import logging
import os
import signal
import sys
import threading

# Add src to path for standalone run
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from ai_service.adapter.postgres import PostgresJobRepository, PostgresMatchRepository
from ai_service.adapter.redis import RedisMatchNotifyQueue, RedisQueueConsumer
from ai_service.adapter.rule_based import RuleBasedClassifier
from ai_service.adapter.sentence_transformers import SentenceTransformerEmbedding
from ai_service.util.transport_security import (
    is_production_env,
    validate_postgres_tls_for_production,
    validate_redis_tls_for_production,
)
from ai_service.usecase.consumer_loop import run_consumer
from ai_service.usecase.process_job import ProcessJobUseCase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
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

    queue_name = os.getenv("AI_QUEUE", "ai-process")
    model_name = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
    threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))
    max_matches = int(os.getenv("MAX_MATCHES_PER_JOB", "20"))

    repo = PostgresJobRepository(db_url)
    match_repo = PostgresMatchRepository(db_url)
    match_notify_queue = RedisMatchNotifyQueue(redis_url)
    embedding = SentenceTransformerEmbedding(model_name)
    classifier = RuleBasedClassifier()
    process_job = ProcessJobUseCase(
        repo, embedding, classifier, match_repo,
        match_notify_queue=match_notify_queue,
        similarity_threshold=threshold, max_matches_per_job=max_matches,
    )
    queue = RedisQueueConsumer(redis_url, queue_name)

    stop_event = threading.Event()

    def on_signal(signum: int, frame: object) -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    logger.info("consumer started, queue=%s", queue_name)
    run_consumer(queue, process_job, timeout_sec=5, stop_event=stop_event)
    logger.info("consumer stopped")


if __name__ == "__main__":
    main()
