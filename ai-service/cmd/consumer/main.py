#!/usr/bin/env python3
"""AI Service consumer: BRPOP → ProcessJob. Точка входа."""

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
    load_dotenv("../.env")  # when launched from ai-service/
except ImportError:
    pass  # python-dotenv is optional in container/runtime envs

from ai_service.adapter.fallback import FallbackClassifier
from ai_service.adapter.ollama import OllamaClassifier
from ai_service.adapter.postgres import (
    PostgresJobRepository,
    PostgresMatchRepository,
    PostgresPendingJobsRepository,
)
from ai_service.adapter.redis import RedisQueueConsumer
from ai_service.adapter.rule_based import RuleBasedClassifier
from ai_service.adapter.sentence_transformers import SentenceTransformerEmbedding
from ai_service.tracing.setup import init_tracer
from ai_service.usecase.accumulate_matches import AccumulateMatchesUseCase
from ai_service.usecase.consumer_loop import run_consumer
from ai_service.usecase.process_job import ProcessJobUseCase
from ai_service.util.fallback_metrics import start_metrics_server_from_env
from ai_service.util.ollama_probe import probe_ollama
from ai_service.util.transport_security import (
    is_production_env,
    validate_postgres_tls_for_production,
    validate_redis_tls_for_production,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

READY_FILE_ENV = "AI_READY_FILE"
DEFAULT_READY_FILE = "/tmp/ai-consumer-ready"
WARMUP_TEXT_ENV = "AI_WARMUP_TEXT"
DEFAULT_WARMUP_TEXT = "Warmup embedding probe"
WARMUP_ENABLED_ENV = "AI_WARMUP_ENABLED"
DEFAULT_WARMUP_ENABLED = True
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


def _warmup_embedding(embedding: SentenceTransformerEmbedding) -> None:
    warmup_text = os.getenv(WARMUP_TEXT_ENV, DEFAULT_WARMUP_TEXT)
    vec = embedding.encode(warmup_text)
    logger.info("embedding warmup completed, dim=%d", len(vec))


def _warmup_enabled() -> bool:
    raw = os.getenv(WARMUP_ENABLED_ENV, "1" if DEFAULT_WARMUP_ENABLED else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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
    start_metrics_server_from_env(
        port_env="AI_CONSUMER_METRICS_PORT",
        default_port=0,
    )

    queue_name = os.getenv("AI_QUEUE", "ai-process")
    model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))
    max_matches = int(os.getenv("MAX_MATCHES_PER_JOB", "20"))
    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b-instruct-q4_K_M")
    ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT_SEC", "30"))
    ollama_required = os.getenv("OLLAMA_REQUIRED", "1" if is_production_env(app_env) else "0") == "1"
    if not probe_ollama(
        ollama_url,
        required=ollama_required,
        required_models=[ollama_model],
    ) and ollama_required:
        sys.exit(1)

    repo = PostgresJobRepository(db_url)
    match_repo = PostgresMatchRepository(db_url)
    pending_repo = PostgresPendingJobsRepository(db_url)
    accumulate_matches = AccumulateMatchesUseCase(pending_repo)
    embedding = SentenceTransformerEmbedding(model_name)
    if _warmup_enabled():
        _warmup_embedding(embedding)
    else:
        logger.info("embedding warmup disabled by %s", WARMUP_ENABLED_ENV)
    classifier = FallbackClassifier(
        primary=OllamaClassifier(base_url=ollama_url, model=ollama_model, timeout_sec=ollama_timeout),
        fallback=RuleBasedClassifier(),
    )
    logger.info("classifier=FallbackClassifier(primary=Ollama[%s], fallback=RuleBased)", ollama_url)
    process_job = ProcessJobUseCase(
        repo, embedding, classifier, match_repo,
        accumulate_matches=accumulate_matches,
        similarity_threshold=threshold, max_matches_per_job=max_matches,
    )
    init_tracer("site-parser-ai")
    queue = RedisQueueConsumer(redis_url, queue_name)
    _mark_ready(ready_file)

    stop_event = threading.Event()
    consumer_stopped = threading.Event()
    grace_sec = _shutdown_grace_sec()
    force_exit_lock = threading.Lock()
    force_exit_started = False

    def force_exit(exit_code: int) -> None:
        requeued = _nack_inflight_messages(queue)
        if requeued > 0:
            logger.warning("requeued %d in-flight jobs before forced shutdown", requeued)
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
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        logger.info("shutdown signal received: %s", signal_name)
        stop_event.set()
        with force_exit_lock:
            if not force_exit_started:
                force_exit_started = True
                t = threading.Thread(target=force_exit_on_timeout, daemon=True, name="consumer-force-exit")
                t.start()
                return
        logger.warning("second shutdown signal received, forcing immediate exit")
        force_exit(1)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    logger.info("consumer started, queue=%s", queue_name)
    try:
        run_consumer(queue, process_job, timeout_sec=5, stop_event=stop_event)
    finally:
        requeued = _nack_inflight_messages(queue)
        if requeued > 0:
            logger.warning("requeued %d in-flight jobs during shutdown", requeued)
        consumer_stopped.set()
    logger.info("consumer stopped")


if __name__ == "__main__":
    main()
