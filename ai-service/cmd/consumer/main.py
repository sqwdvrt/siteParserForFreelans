#!/usr/bin/env python3
"""AI Service consumer: BRPOP → ProcessJob. Точка входа."""

import atexit
import inspect
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

from ai_service.adapter.postgres import (
    PostgresFeedbackRepository,
    PostgresFilterEventRepository,
    PostgresJobRepository,
    PostgresMatchRepository,
    PostgresPendingJobsRepository,
    PostgresUserRepository,
)
from ai_service.adapter.redis import RedisQueueConsumer
from ai_service.adapter.sentence_transformers import CrossEncoderReranker, SentenceTransformerEmbedding
from ai_service.tracing.setup import init_tracer
from ai_service.usecase.accumulate_matches import AccumulateMatchesUseCase
from ai_service.usecase.consumer_loop import run_consumer
from ai_service.usecase.process_job import ProcessJobUseCase
from ai_service.util.fallback_metrics import start_metrics_server_from_env
from ai_service.util.postgres_pool_config import load_postgres_pool_settings
from ai_service.util.runtime_env import require_env, resolve_redis_url
from ai_service.util.shutdown import cap_blocking_pop_timeout
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
CLASSIFIER_ENABLED_ENV = "ENABLE_GEMINI_CLASSIFIER"
SHUTDOWN_GRACE_SEC_ENV = "AI_SHUTDOWN_GRACE_SEC"
DEFAULT_SHUTDOWN_GRACE_SEC = 20.0
POP_TIMEOUT_SEC_ENV = "AI_PROCESS_POP_TIMEOUT_SEC"
DEFAULT_POP_TIMEOUT_SEC = 60
RERANK_FALLBACK_ENABLED_ENV = "RERANK_FALLBACK_ENABLED"
DEFAULT_RERANK_FALLBACK_ENABLED = True


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


def _classifier_enabled() -> bool:
    raw = os.getenv(CLASSIFIER_ENABLED_ENV, "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _rerank_fallback_enabled() -> bool:
    raw = os.getenv(
        RERANK_FALLBACK_ENABLED_ENV,
        "1" if DEFAULT_RERANK_FALLBACK_ENABLED else "0",
    ).strip().lower()
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


def _pop_timeout_sec() -> int:
    raw = os.getenv(POP_TIMEOUT_SEC_ENV, str(DEFAULT_POP_TIMEOUT_SEC))
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "invalid %s=%r, fallback to %ds",
            POP_TIMEOUT_SEC_ENV,
            raw,
            DEFAULT_POP_TIMEOUT_SEC,
        )
        return DEFAULT_POP_TIMEOUT_SEC
    if value <= 0:
        logger.warning(
            "non-positive %s=%r, fallback to %ds",
            POP_TIMEOUT_SEC_ENV,
            raw,
            DEFAULT_POP_TIMEOUT_SEC,
        )
        return DEFAULT_POP_TIMEOUT_SEC
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
    try:
        db_url = require_env("DATABASE_URL", os.getenv("DATABASE_URL"))
        redis_url = resolve_redis_url(app_env, os.getenv("REDIS_URL"))
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)
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
    start_metrics_server_from_env(
        port_env="AI_CONSUMER_METRICS_PORT",
        default_port=0,
    )

    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    classifier_enabled = _classifier_enabled()
    if classifier_enabled and not gemini_api_key:
        logger.warning("GEMINI_API_KEY not set, classifier will be disabled")

    queue_name = os.getenv("AI_QUEUE", "ai-process")
    model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    rerank_model = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
    rerank_threshold = float(os.getenv("RERANK_THRESHOLD", "0.55"))
    rerank_top_k = int(os.getenv("RERANK_TOP_K", "10"))
    rerank_fallback_enabled = _rerank_fallback_enabled()
    threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))
    max_matches = int(os.getenv("MAX_MATCHES_PER_JOB", "50"))
    grace_sec = _shutdown_grace_sec()
    pop_timeout_sec = cap_blocking_pop_timeout(
        _pop_timeout_sec(),
        grace_sec,
        logger=logger,
        timeout_name=POP_TIMEOUT_SEC_ENV,
        grace_name=SHUTDOWN_GRACE_SEC_ENV,
    )

    repo = PostgresJobRepository(db_url, **pg_pool_kwargs)
    user_repo = PostgresUserRepository(db_url, **pg_pool_kwargs)
    match_repo = PostgresMatchRepository(db_url, **pg_pool_kwargs)
    pending_repo = PostgresPendingJobsRepository(db_url, **pg_pool_kwargs)
    feedback_repo = PostgresFeedbackRepository(db_url, **pg_pool_kwargs)
    filter_event_repo = PostgresFilterEventRepository(db_url, **pg_pool_kwargs)
    accumulate_matches = AccumulateMatchesUseCase(pending_repo)
    embedding = SentenceTransformerEmbedding(model_name)
    reranker = None
    try:
        reranker = CrossEncoderReranker(rerank_model)
        logger.info("reranker loaded: %s", rerank_model)
    except Exception as e:
        logger.warning(
            "reranker failed to load model=%s, continuing without reranker "
            "(embedding similarity only): %s",
            rerank_model,
            e,
        )
    classifier = None
    if classifier_enabled and gemini_api_key:
        from ai_service.adapter.gemini import GeminiClassifier

        classifier = GeminiClassifier(api_key=gemini_api_key)
    if _warmup_enabled():
        _warmup_embedding(embedding)
    else:
        logger.info("embedding warmup disabled by %s", WARMUP_ENABLED_ENV)
    process_job_kwargs = {
        "classifier": classifier,
        "match_repo": match_repo,
        "accumulate_matches": accumulate_matches,
        "reranker": reranker,
        "similarity_threshold": threshold,
        "max_matches_per_job": max_matches,
        "rerank_threshold": rerank_threshold,
        "rerank_top_k": rerank_top_k,
        "feedback_repo": feedback_repo,
        "user_repo": user_repo,
        "filter_event_repo": filter_event_repo,
    }
    if _supports_constructor_kwarg(ProcessJobUseCase, "rerank_fallback_enabled"):
        process_job_kwargs["rerank_fallback_enabled"] = rerank_fallback_enabled
    process_job = ProcessJobUseCase(
        repo,
        embedding,
        **process_job_kwargs,
    )
    init_tracer("site-parser-ai")
    queue = RedisQueueConsumer(redis_url, queue_name)
    _mark_ready(ready_file)

    stop_event = threading.Event()
    consumer_stopped = threading.Event()
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

    logger.info("consumer started, queue=%s pop_timeout_sec=%d", queue_name, pop_timeout_sec)
    try:
        run_consumer(queue, process_job, timeout_sec=pop_timeout_sec, stop_event=stop_event)
    finally:
        requeued = _nack_inflight_messages(queue)
        if requeued > 0:
            logger.warning("requeued %d in-flight jobs during shutdown", requeued)
        consumer_stopped.set()
    logger.info("consumer stopped")


if __name__ == "__main__":
    main()
