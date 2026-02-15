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

from ai_service.adapter.postgres import PostgresJobRepository
from ai_service.adapter.redis import RedisQueueConsumer
from ai_service.adapter.rule_based import RuleBasedClassifier
from ai_service.adapter.sentence_transformers import SentenceTransformerEmbedding
from ai_service.usecase.consumer_loop import run_consumer
from ai_service.usecase.process_job import ProcessJobUseCase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("AI_QUEUE", "ai-process")
    model_name = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

    repo = PostgresJobRepository(db_url)
    embedding = SentenceTransformerEmbedding(model_name)
    classifier = RuleBasedClassifier()
    process_job = ProcessJobUseCase(repo, embedding, classifier)
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
