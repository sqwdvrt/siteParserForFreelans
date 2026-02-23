#!/usr/bin/env python3
"""AI Service user-embed consumer: BRPOP user-embed → ProcessUserEmbed. Точка входа."""

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

from ai_service.adapter.postgres import PostgresUserRepository
from ai_service.adapter.redis import RedisUserEmbedQueueConsumer
from ai_service.adapter.sentence_transformers import SentenceTransformerEmbedding
from ai_service.util.transport_security import (
    is_production_env,
    validate_postgres_tls_for_production,
    validate_redis_tls_for_production,
)
from ai_service.usecase.process_user_embed import ProcessUserEmbedUseCase
from ai_service.usecase.user_embed_consumer_loop import run_user_embed_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

READY_FILE_ENV = "AI_READY_FILE"
DEFAULT_READY_FILE = "/tmp/ai-user-embed-ready"
WARMUP_TEXT_ENV = "AI_WARMUP_TEXT"
DEFAULT_WARMUP_TEXT = "Warmup embedding probe"


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


def _warmup_embedding(embedding: SentenceTransformerEmbedding) -> None:
    warmup_text = os.getenv(WARMUP_TEXT_ENV, DEFAULT_WARMUP_TEXT)
    vec = embedding.encode(warmup_text)
    logger.info("embedding warmup completed, dim=%d", len(vec))


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

    queue_name = os.getenv("USER_EMBED_QUEUE", "user-embed")
    model_name = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

    user_repo = PostgresUserRepository(db_url)
    embedding = SentenceTransformerEmbedding(model_name)
    _warmup_embedding(embedding)
    process_user_embed = ProcessUserEmbedUseCase(user_repo, embedding)
    queue = RedisUserEmbedQueueConsumer(redis_url, queue_name)
    _mark_ready(ready_file)

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
