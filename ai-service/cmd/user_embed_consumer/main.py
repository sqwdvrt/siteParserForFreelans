#!/usr/bin/env python3
"""AI Service user-embed consumer: BRPOP user-embed → ProcessUserEmbed. Точка входа."""

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

from ai_service.adapter.postgres import PostgresUserRepository
from ai_service.adapter.redis import RedisUserEmbedQueueConsumer
from ai_service.adapter.sentence_transformers import SentenceTransformerEmbedding
from ai_service.usecase.process_user_embed import ProcessUserEmbedUseCase
from ai_service.usecase.user_embed_consumer_loop import run_user_embed_consumer

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
    queue_name = os.getenv("USER_EMBED_QUEUE", "user-embed")
    model_name = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

    user_repo = PostgresUserRepository(db_url)
    embedding = SentenceTransformerEmbedding(model_name)
    process_user_embed = ProcessUserEmbedUseCase(user_repo, embedding)
    queue = RedisUserEmbedQueueConsumer(redis_url, queue_name)

    stop_event = threading.Event()

    def on_signal(signum: int, frame: object) -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    logger.info("user-embed consumer started, queue=%s", queue_name)
    run_user_embed_consumer(queue, process_user_embed, timeout_sec=5, stop_event=stop_event)
    logger.info("user-embed consumer stopped")


if __name__ == "__main__":
    main()
