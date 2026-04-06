#!/usr/bin/env python3
"""Telegram bot: getUpdates/webhook → backend API. Thin client with basic recovery and metrics."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import math
import os
import random
import re
import secrets
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

try:
    import redis
except ImportError:
    redis = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")  # при запуске из telegram-bot/
except ImportError:
    pass


def _parse_log_level(raw: str | None, default: int = logging.INFO) -> tuple[int, bool]:
    value = (raw or "").strip()
    if not value:
        return default, False
    level = getattr(logging, value.upper(), None)
    if isinstance(level, int):
        return level, True
    return default, False


_LOG_LEVEL_RAW = os.getenv("LOG_LEVEL")
_LOG_LEVEL, _LOG_LEVEL_OK = _parse_log_level(_LOG_LEVEL_RAW, logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
if _LOG_LEVEL_RAW and not _LOG_LEVEL_OK:
    logger.warning("invalid LOG_LEVEL=%r; using INFO", _LOG_LEVEL_RAW)

TELEGRAM_BASE = "https://api.telegram.org/bot"
FORBIDDEN_SECRET_PREFIXES = (
    "change_me",
    "changeme",
    "replace_me",
    "replace_with",
    "your_",
    "example_",
    "dummy_",
    "test_",
)
ALLOWED_OUTBOUND_SCHEMES = {"http", "https"}
_OUTBOUND_HOST_ALLOWLIST = {"api.telegram.org"}
API_RETRY_ATTEMPTS = 4
API_RETRY_BASE_DELAY_SEC = 0.4
API_RETRY_MAX_DELAY_SEC = 3.0
API_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
POLL_RETRY_BASE_DELAY_SEC = 0.5
POLL_RETRY_MAX_DELAY_SEC = 10.0
HEARTBEAT_FILE_ENV = "TELEGRAM_HEARTBEAT_FILE"
DEFAULT_HEARTBEAT_FILE = "/tmp/telegram-bot-heartbeat"
LOCAL_BOT_TOKEN_ENV = "LOCAL_TELEGRAM_BOT_TOKEN"
POLLING_ACTIVE_WEBHOOK_POLICY_ENV = "POLLING_ACTIVE_WEBHOOK_POLICY"
DEFAULT_POLLING_ACTIVE_WEBHOOK_POLICY = "standby"
POLLING_STANDBY_SLEEP_SEC = 30.0
WEBHOOK_PATH = "/webhook"
WEBHOOK_MAX_BODY_BYTES = 1_048_576
DEFAULT_METRICS_BIND = "0.0.0.0"
DEFAULT_METRICS_PORT = 9107

# ---------------------------------------------------------------------------
# Onboarding wizard data
# ---------------------------------------------------------------------------
ONBOARDING_CATEGORIES: dict[str, str] = {
    "backend": "Backend-разработка",
    "frontend": "Frontend-разработка",
    "mobile": "Мобильная разработка",
    "data_ai": "Data / AI / ML",
    "devops": "DevOps / Инфраструктура",
    "design": "Дизайн",
    "qa": "QA / Тестирование",
    "pm": "PM / BA",
    "other": "Другое",
}

ONBOARDING_SKILLS: dict[str, list[str]] = {
    "backend": [
        "Python",
        "Go",
        "Node.js",
        "Java",
        "C#",
        "PHP",
        "Rust",
        "PostgreSQL",
        "MySQL",
        "Redis",
        "Docker",
        "REST API",
    ],
    "frontend": ["React", "Vue", "Angular", "TypeScript", "JavaScript", "HTML/CSS", "Next.js", "Webpack"],
    "mobile":   ["iOS/Swift", "Android/Kotlin", "React Native", "Flutter"],
    "data_ai":  ["Python", "ML/AI", "TensorFlow", "PyTorch", "pandas", "SQL", "Spark", "Data Engineering"],
    "devops":   ["Docker", "Kubernetes", "CI/CD", "AWS", "GCP", "Azure", "Terraform", "Linux"],
    "design":   ["UI/UX", "Figma", "Adobe XD", "Sketch", "Illustrator", "Photoshop"],
    "qa":       ["Manual QA", "Selenium", "Pytest", "Postman", "Cypress", "JMeter", "Appium"],
    "pm":       ["Agile/Scrum", "JIRA", "Confluence", "Product Management", "Business Analysis"],
    "other":    [],
}

ONBOARDING_EXPERIENCE: dict[str, str] = {
    "junior": "Junior (до 2 лет)",
    "middle": "Middle (2–5 лет)",
    "senior": "Senior (5+ лет)",
    "lead":   "Lead / Architect",
}

ONBOARDING_RATE: dict[str, str] = {
    "low":  "до 1 000 ₽/ч",
    "mid":  "1 000–2 500 ₽/ч",
    "high": "2 500–5 000 ₽/ч",
    "top":  "5 000+ ₽/ч",
    "skip": "Не указывать",
}

_ONBOARDING_MIN_PROFILE_LEN = 80


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s must be an integer; using default=%s", name, default)
        return default
    if value <= 0:
        logger.warning("%s must be > 0; using default=%s", name, default)
        return default
    return value


def _read_bounded_int_env(name: str, default: int, *, min_value: int, max_value: int) -> int:
    value = _read_positive_int_env(name, default)
    if value < min_value or value > max_value:
        logger.warning("%s must be in range [%s, %s]; using default=%s", name, min_value, max_value, default)
        return default
    return value


def _read_non_negative_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s must be an integer; using default=%s", name, default)
        return default
    if value < 0:
        logger.warning("%s must be >= 0; using default=%s", name, default)
        return default
    return value


class _UserIDCache(OrderedDict[int, tuple[int, float]]):
    def __init__(self, *, maxsize: int, ttl_sec: int):
        super().__init__()
        self.maxsize = maxsize
        self.ttl_sec = ttl_sec


class _ConversationStateCache(OrderedDict[int, tuple[str, float]]):
    def __init__(self, *, maxsize: int, ttl_sec: int):
        super().__init__()
        self.maxsize = maxsize
        self.ttl_sec = ttl_sec


_UPDATE_CLAIMED = "claimed"
_UPDATE_INFLIGHT = "inflight"
_UPDATE_DONE = "done"
_UPDATE_QUEUED = "queued"
_UPDATE_PROCESSING = "processing"


class _ProcessedUpdateCache(OrderedDict[int, tuple[str, float]]):
    def __init__(self, *, maxsize: int, ttl_sec: int):
        super().__init__()
        self.maxsize = maxsize
        self.ttl_sec = ttl_sec


def _build_user_id_cache(*, maxsize: int, ttl_sec: int) -> _UserIDCache:
    safe_maxsize = max(1, int(maxsize))
    safe_ttl_sec = max(1, int(ttl_sec))
    return _UserIDCache(maxsize=safe_maxsize, ttl_sec=safe_ttl_sec)


def _build_conversation_state_cache(*, maxsize: int, ttl_sec: int) -> _ConversationStateCache:
    safe_maxsize = max(1, int(maxsize))
    safe_ttl_sec = max(1, int(ttl_sec))
    return _ConversationStateCache(maxsize=safe_maxsize, ttl_sec=safe_ttl_sec)


def _build_processed_update_cache(*, maxsize: int, ttl_sec: int) -> _ProcessedUpdateCache:
    safe_maxsize = max(1, int(maxsize))
    safe_ttl_sec = max(1, int(ttl_sec))
    return _ProcessedUpdateCache(maxsize=safe_maxsize, ttl_sec=safe_ttl_sec)


USER_ID_CACHE_TTL_SEC = _read_positive_int_env("USER_ID_CACHE_TTL_SEC", 300)
USER_ID_CACHE_MAXSIZE = _read_positive_int_env("USER_ID_CACHE_MAXSIZE", 5000)
_USER_ID_CACHE: _UserIDCache = _build_user_id_cache(
    maxsize=USER_ID_CACHE_MAXSIZE,
    ttl_sec=USER_ID_CACHE_TTL_SEC,
)
CONVERSATION_STATE_TTL_SEC = _read_positive_int_env("CONVERSATION_STATE_TTL_SEC", 900)
CONVERSATION_STATE_MAXSIZE = _read_positive_int_env("CONVERSATION_STATE_MAXSIZE", 5000)
_CONVERSATION_STATE_CACHE: _ConversationStateCache = _build_conversation_state_cache(
    maxsize=CONVERSATION_STATE_MAXSIZE,
    ttl_sec=CONVERSATION_STATE_TTL_SEC,
)
PROCESSED_UPDATE_TTL_SEC = _read_positive_int_env("PROCESSED_UPDATE_TTL_SEC", 900)
PROCESSED_UPDATE_MAXSIZE = _read_positive_int_env("PROCESSED_UPDATE_MAXSIZE", 10000)
_PROCESSED_UPDATE_CACHE: _ProcessedUpdateCache = _build_processed_update_cache(
    maxsize=PROCESSED_UPDATE_MAXSIZE,
    ttl_sec=PROCESSED_UPDATE_TTL_SEC,
)
_CACHE_LOCK = threading.RLock()


class _BotMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._ready = 0

    def inc(self, name: str, /, **labels: str) -> None:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1

    def set_ready(self, ready: bool) -> None:
        with self._lock:
            self._ready = 1 if ready else 0

    def snapshot(self) -> tuple[dict[tuple[str, tuple[tuple[str, str], ...]], int], int]:
        with self._lock:
            return dict(self._counters), self._ready


_METRICS = _BotMetrics()


_PROMETHEUS_META: dict[str, tuple[str, str]] = {
    "telegram_bot_updates_total": ("counter", "Telegram bot updates by transport and result."),
    "telegram_bot_commands_total": ("counter", "Telegram bot command executions by command and result."),
    "telegram_bot_http_requests_total": ("counter", "Outbound HTTP requests by method, target and status class."),
    "telegram_bot_http_retries_total": ("counter", "Outbound HTTP retries by method, target and reason."),
    "telegram_bot_cache_operations_total": ("counter", "Bot cache and state operations by cache and result."),
    "telegram_bot_state_store_operations_total": (
        "counter",
        "Telegram bot Redis state store operations by operation and result.",
    ),
    "telegram_bot_ready": ("gauge", "Telegram bot readiness state."),
}


def _metrics_lines() -> list[str]:
    counters, ready = _METRICS.snapshot()
    by_name: dict[str, list[tuple[tuple[tuple[str, str], ...], int]]] = {}
    for (name, labels), value in counters.items():
        by_name.setdefault(name, []).append((labels, value))

    lines: list[str] = []
    for name, (metric_type, help_text) in _PROMETHEUS_META.items():
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
        if name == "telegram_bot_ready":
            lines.append(f"{name} {ready}")
            continue
        for labels, value in sorted(by_name.get(name, []), key=lambda item: item[0]):
            if labels:
                rendered_labels = ",".join(f'{k}="{v}"' for k, v in labels)
                lines.append(f"{name}{{{rendered_labels}}} {value}")
            else:
                lines.append(f"{name} {value}")
    return lines


def _render_metrics() -> str:
    return "\n".join(_metrics_lines()) + "\n"


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._write_plain(200, "ok\n")
            return
        if self.path == "/readyz":
            ready = _METRICS.snapshot()[1] == 1
            self._write_plain(200 if ready else 503, "ok\n" if ready else "not ready\n")
            return
        if self.path == "/metrics":
            payload = _render_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def _write_plain(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        _ = fmt
        _ = args


def _start_metrics_server(bind: str, port: int) -> ThreadingHTTPServer | None:
    if port <= 0:
        return None
    server = ThreadingHTTPServer((bind, port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="telegram-bot-metrics")
    thread.start()
    logger.info("metrics server listening on %s:%d", bind, port)
    return server


def _prune_expired_entries(
    cache: (
        OrderedDict[int, tuple[int, float]]
        | OrderedDict[int, tuple[str, float]]
    ),
    now: float,
) -> None:
    expired_keys: list[int] = []
    for key, value in cache.items():
        expires_at = value[1] if isinstance(value, tuple) else value
        if now >= expires_at:
            expired_keys.append(key)
            continue
        break
    for key in expired_keys:
        cache.pop(key, None)


def _observe_http_request(method: str, url: str, status: int) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    target = "telegram" if host == "api.telegram.org" else "backend"
    if status <= 0:
        status_class = "network_error"
    else:
        status_class = f"{status // 100}xx"
    _METRICS.inc("telegram_bot_http_requests_total", method=method.upper(), target=target, status=status_class)


def _response_status(response: object, default: int = 200) -> int:
    raw = getattr(response, "status", default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _observe_http_retry(method: str, url: str, reason: str) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    target = "telegram" if host == "api.telegram.org" else "backend"
    _METRICS.inc("telegram_bot_http_retries_total", method=method.upper(), target=target, reason=reason)


def _record_command(command: str, result: str) -> None:
    _METRICS.inc("telegram_bot_commands_total", command=command, result=result)


class _RedisStateStore:
    def __init__(
        self,
        client,
        *,
        prefix: str,
        user_id_ttl_sec: int,
        conversation_state_ttl_sec: int,
        processed_update_ttl_sec: int,
    ) -> None:
        self._client = client
        self._prefix = prefix.strip() or "telegram-bot"
        self._user_id_ttl_sec = max(1, int(user_id_ttl_sec))
        self._conversation_state_ttl_sec = max(1, int(conversation_state_ttl_sec))
        self._processed_update_ttl_sec = max(1, int(processed_update_ttl_sec))

    def cache_user_id(self, telegram_id: int, user_id: int) -> None:
        self._client.set(self._key("user-id", telegram_id), str(user_id), ex=self._user_id_ttl_sec)
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="cache_user_id", result="ok")

    def get_user_id(self, telegram_id: int) -> int | None:
        raw = self._client.get(self._key("user-id", telegram_id))
        if raw is None:
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_user_id", result="miss")
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self._client.delete(self._key("user-id", telegram_id))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_user_id", result="invalid")
            return None
        if value <= 0:
            self._client.delete(self._key("user-id", telegram_id))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_user_id", result="invalid")
            return None
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_user_id", result="hit")
        return value

    def set_conversation_state(self, telegram_id: int, state: str) -> None:
        self._client.set(self._key("conversation", telegram_id), state, ex=self._conversation_state_ttl_sec)
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="set_conversation_state", result="ok")

    def get_conversation_state(self, telegram_id: int) -> str | None:
        raw = self._client.get(self._key("conversation", telegram_id))
        if raw is None:
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_conversation_state", result="miss")
            return None
        value = str(raw).strip()
        if not value:
            self._client.delete(self._key("conversation", telegram_id))
            _METRICS.inc(
                "telegram_bot_state_store_operations_total",
                operation="get_conversation_state",
                result="invalid",
            )
            return None
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_conversation_state", result="hit")
        return value

    def clear_conversation_state(self, telegram_id: int) -> None:
        self._client.delete(self._key("conversation", telegram_id))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="clear_conversation_state", result="ok")

    def set_batch_session(self, session_id: str, session: dict) -> None:
        key = self._key_text("batch-session", session_id)
        self._client.set(
            key,
            json.dumps(session, separators=(",", ":"), ensure_ascii=False),
            ex=self._conversation_state_ttl_sec,
        )
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="set_batch_session", result="ok")

    def get_batch_session(self, session_id: str) -> dict | None:
        raw = self._client.get(self._key_text("batch-session", session_id))
        if raw is None:
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_batch_session", result="miss")
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            self._client.delete(self._key_text("batch-session", session_id))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_batch_session", result="invalid")
            return None
        if not isinstance(data, dict):
            self._client.delete(self._key_text("batch-session", session_id))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_batch_session", result="invalid")
            return None
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_batch_session", result="hit")
        return data

    def clear_batch_session(self, session_id: str) -> None:
        self._client.delete(self._key_text("batch-session", session_id))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="clear_batch_session", result="ok")

    def claim_update_id(self, update_id: int) -> str:
        claimed = bool(
            self._client.set(
                self._key("processed-update", update_id),
                _UPDATE_INFLIGHT,
                ex=self._processed_update_ttl_sec,
                nx=True,
            )
        )
        if claimed:
            result = _UPDATE_CLAIMED
        else:
            raw = self._client.get(self._key("processed-update", update_id))
            if isinstance(raw, bytes):
                state = raw.decode("utf-8", errors="ignore").strip().lower()
            else:
                state = str(raw or "").strip().lower()
            result = _UPDATE_DONE if state == _UPDATE_DONE else _UPDATE_INFLIGHT
        _METRICS.inc(
            "telegram_bot_state_store_operations_total",
            operation="claim_update_id",
            result=result,
        )
        return result

    def complete_update_id(self, update_id: int) -> None:
        self._client.set(
            self._key("processed-update", update_id),
            _UPDATE_DONE,
            ex=self._processed_update_ttl_sec,
        )
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="complete_update_id", result="ok")

    def forget_update_id(self, update_id: int) -> None:
        self._client.delete(self._key("processed-update", update_id))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="forget_update_id", result="ok")

    def enqueue_webhook_update(self, update_id: int, payload: str) -> str:
        state_key = self._key("processed-update", update_id)
        payload_key = self._key("webhook-payload", update_id)
        queue_key = self._key("webhook-inbox", 0)
        existing = str(self._client.get(state_key) or "").strip().lower()
        if existing in {_UPDATE_DONE, _UPDATE_QUEUED, _UPDATE_PROCESSING, _UPDATE_INFLIGHT}:
            _METRICS.inc(
                "telegram_bot_state_store_operations_total",
                operation="enqueue_webhook_update",
                result="duplicate",
            )
            return "duplicate"
        self._client.set(payload_key, payload, ex=self._processed_update_ttl_sec)
        self._client.rpush(queue_key, str(update_id))
        self._client.set(state_key, _UPDATE_QUEUED, ex=self._processed_update_ttl_sec)
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="enqueue_webhook_update", result="enqueued")
        return "enqueued"

    def claim_next_webhook_update(self, timeout_sec: int) -> tuple[int, str] | None:
        queue_key = self._key("webhook-inbox", 0)
        processing_key = self._key("webhook-processing", 0)
        raw_update_id = self._client.brpoplpush(queue_key, processing_key, timeout=max(0, int(timeout_sec)))
        if raw_update_id is None:
            _METRICS.inc(
                "telegram_bot_state_store_operations_total",
                operation="claim_next_webhook_update",
                result="empty",
            )
            return None
        update_id = int(raw_update_id)
        payload = self._client.get(self._key("webhook-payload", update_id))
        if payload is None:
            self._client.lrem(processing_key, 0, str(update_id))
            self._client.delete(self._key("processed-update", update_id))
            _METRICS.inc(
                "telegram_bot_state_store_operations_total",
                operation="claim_next_webhook_update",
                result="missing_payload",
            )
            return None
        self._client.set(
            self._key("processed-update", update_id),
            _UPDATE_PROCESSING,
            ex=self._processed_update_ttl_sec,
        )
        _METRICS.inc(
            "telegram_bot_state_store_operations_total",
            operation="claim_next_webhook_update",
            result="claimed",
        )
        return update_id, str(payload)

    def ack_webhook_update(self, update_id: int) -> None:
        self._client.lrem(self._key("webhook-processing", 0), 0, str(update_id))
        self._client.delete(self._key("webhook-payload", update_id))
        self._client.set(self._key("processed-update", update_id), _UPDATE_DONE, ex=self._processed_update_ttl_sec)
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="ack_webhook_update", result="ok")

    def requeue_webhook_update(self, update_id: int) -> None:
        self._client.lrem(self._key("webhook-processing", 0), 0, str(update_id))
        self._client.lpush(self._key("webhook-inbox", 0), str(update_id))
        self._client.set(self._key("processed-update", update_id), _UPDATE_QUEUED, ex=self._processed_update_ttl_sec)
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="requeue_webhook_update", result="ok")

    def recover_webhook_processing(self) -> int:
        processing_key = self._key("webhook-processing", 0)
        pending_key = self._key("webhook-inbox", 0)
        update_ids = [str(raw) for raw in self._client.lrange(processing_key, 0, -1)]
        recovered = 0
        for raw_update_id in update_ids:
            try:
                update_id = int(raw_update_id)
            except (TypeError, ValueError):
                self._client.lrem(processing_key, 0, raw_update_id)
                continue
            if self._client.get(self._key("webhook-payload", update_id)) is None:
                self._client.lrem(processing_key, 0, raw_update_id)
                self._client.delete(self._key("processed-update", update_id))
                continue
            self._client.lrem(processing_key, 0, raw_update_id)
            self._client.lpush(pending_key, raw_update_id)
            self._client.set(
                self._key("processed-update", update_id),
                _UPDATE_QUEUED,
                ex=self._processed_update_ttl_sec,
            )
            recovered += 1
        _METRICS.inc(
            "telegram_bot_state_store_operations_total",
            operation="recover_webhook_processing",
            result="ok" if recovered else "noop",
        )
        return recovered

    def set_first_seen_if_absent(self, telegram_id: int) -> bool:
        """SET NX first-seen timestamp. Returns True if this is the first call (new user)."""
        key = self._key("first-seen", telegram_id)
        return bool(self._client.set(key, str(int(time.time())), nx=True))

    def get_first_seen(self, telegram_id: int) -> int | None:
        raw = self._client.get(self._key("first-seen", telegram_id))
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _key(self, kind: str, entity_id: int) -> str:
        return f"{self._prefix}:{kind}:{entity_id}"

    def _key_text(self, kind: str, raw_id: str) -> str:
        return f"{self._prefix}:{kind}:{str(raw_id).strip()}"


_STATE_STORE: _RedisStateStore | None = None


def _validate_redis_url_for_production(redis_url: str) -> None:
    parsed = urllib.parse.urlsplit((redis_url or "").strip())
    if parsed.scheme.lower() != "rediss":
        raise ValueError("REDIS_URL must use rediss:// in production")
    if not parsed.hostname:
        raise ValueError("REDIS_URL must include host in production")


def _build_state_store(app_env: str | None) -> _RedisStateStore | None:
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        logger.warning("REDIS_URL not set; durable Telegram bot state disabled")
        return None
    if _is_production_env(app_env):
        _validate_redis_url_for_production(redis_url)
    if redis is None:
        if _is_production_env(app_env):
            raise ValueError("python redis package is required for durable Telegram bot state")
        logger.warning("redis package not installed; durable Telegram bot state disabled")
        return None
    try:
        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()
    except Exception as e:
        if _is_production_env(app_env):
            raise ValueError(f"REDIS_URL unavailable for durable Telegram bot state: {_exception_name(e)}") from e
        logger.warning("redis unavailable; durable Telegram bot state disabled: %s", _exception_name(e))
        return None
    prefix = (os.getenv("BOT_REDIS_PREFIX") or "telegram-bot").strip() or "telegram-bot"
    logger.info("durable Telegram bot state enabled via Redis prefix=%s", prefix)
    return _RedisStateStore(
        client,
        prefix=prefix,
        user_id_ttl_sec=USER_ID_CACHE_TTL_SEC,
        conversation_state_ttl_sec=CONVERSATION_STATE_TTL_SEC,
        processed_update_ttl_sec=PROCESSED_UPDATE_TTL_SEC,
    )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler with outbound allowlist enforcement for each redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        base_url = req.full_url
        resolved_url = urllib.parse.urljoin(base_url, newurl)
        try:
            _validate_outbound_url(resolved_url)
        except ValueError as e:
            raise urllib.error.HTTPError(
                resolved_url,
                403,
                f"redirect blocked by outbound policy: {e}",
                headers,
                fp,
            ) from e
        return super().redirect_request(req, fp, code, msg, headers, resolved_url)


def _build_opener() -> urllib.request.OpenerDirector:
    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "").strip()
    handlers: list = [
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        _SafeRedirectHandler(),
    ]
    if proxy_url:
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        logging.getLogger(__name__).info("Telegram HTTP opener: proxy configured")
    return urllib.request.build_opener(*handlers)


_HTTP_ONLY_OPENER = _build_opener()


def _safe_open(url_or_request, timeout: int):
    return _HTTP_ONLY_OPENER.open(url_or_request, timeout=timeout)


def _exception_name(err: Exception) -> str:
    """Безопасное имя ошибки для логов (без текста/URL/секретов)."""
    return type(err).__name__


def _compact_log_text(value: str, limit: int = 200) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _http_error_log_detail(err: urllib.error.HTTPError) -> str:
    body = ""
    if err.fp is not None:
        try:
            body = err.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
    if body:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            description = parsed.get("description")
            if isinstance(description, str) and description.strip():
                return _compact_log_text(description)
    reason = getattr(err, "reason", "") or ""
    if isinstance(reason, str) and reason.strip():
        return _compact_log_text(reason)
    return _exception_name(err)


def _should_retry_status(status: int) -> bool:
    return status in API_RETRYABLE_STATUS_CODES


def _retry_delay_sec(attempt: int) -> float:
    delay = min(API_RETRY_MAX_DELAY_SEC, API_RETRY_BASE_DELAY_SEC * (2 ** attempt))
    return delay + random.uniform(0.0, 0.2)


def _poll_retry_delay_sec(attempt: int) -> float:
    delay = min(POLL_RETRY_MAX_DELAY_SEC, POLL_RETRY_BASE_DELAY_SEC * (2 ** attempt))
    return delay + random.uniform(0.0, 0.2)


def _touch_heartbeat(path: str) -> None:
    try:
        import pathlib
        pathlib.Path(path).touch(exist_ok=True)
    except Exception as e:
        logger.warning("failed to update heartbeat file %s: %s", path, e)


def _shannon_entropy_bits(secret: str) -> float:
    if not secret:
        return 0.0
    counts: dict[str, int] = {}
    for ch in secret:
        counts[ch] = counts.get(ch, 0) + 1
    total = float(len(secret))
    entropy_per_char = 0.0
    for count in counts.values():
        p = float(count) / total
        entropy_per_char += -p * math.log2(p)
    return entropy_per_char * total


def _validate_secret(name: str, secret: str, min_len: int) -> None:
    value = (secret or "").strip()
    if not value:
        raise ValueError(f"{name} not set")
    lower = value.lower()
    for prefix in FORBIDDEN_SECRET_PREFIXES:
        if lower.startswith(prefix):
            raise ValueError(f"{name} uses forbidden placeholder prefix '{prefix}'")
    if len(value) < min_len:
        raise ValueError(f"{name} must be at least {min_len} chars")
    min_entropy_bits = max(80.0, float(min_len) * 3.0)
    entropy = _shannon_entropy_bits(value)
    if entropy < min_entropy_bits:
        raise ValueError(f"{name} is too weak (entropy {entropy:.1f} < {min_entropy_bits:.1f})")


def _is_production_env(raw: str | None) -> bool:
    value = (raw or "").strip().lower()
    return value in {"prod", "production"}


def _validate_api_url_for_production(api_url: str) -> None:
    parsed = urllib.parse.urlsplit((api_url or "").strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("API_URL must use https:// in production")
    if not parsed.netloc:
        raise ValueError("API_URL must include host in production")


def _validate_webhook_url(webhook_url: str) -> None:
    parsed = urllib.parse.urlsplit((webhook_url or "").strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("WEBHOOK_URL must use https://")
    if not parsed.netloc:
        raise ValueError("WEBHOOK_URL must include host")
    if (parsed.path or "") != WEBHOOK_PATH:
        raise ValueError(f"WEBHOOK_URL must end with {WEBHOOK_PATH}")


def _register_allowed_host(url: str) -> None:
    parsed = urllib.parse.urlsplit((url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if host:
        _OUTBOUND_HOST_ALLOWLIST.add(host)


def _validate_outbound_url(url: str) -> None:
    parsed = urllib.parse.urlsplit((url or "").strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_OUTBOUND_SCHEMES:
        raise ValueError("Outbound URL must use http:// or https://")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("Outbound URL must include host")
    if host not in _OUTBOUND_HOST_ALLOWLIST:
        raise ValueError(f"Outbound URL host '{host}' is not in allowlist")


def _json_body(data: dict) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _http_post(
    url: str,
    data: dict,
    headers: dict[str, str] | None = None,
    *,
    make_headers: Callable[[], dict[str, str]] | None = None,
) -> tuple[int, dict | None]:
    """POST JSON. Возвращает (status_code, json_body или None).

    make_headers: если передан, вызывается на каждой попытке — нужно для
    HMAC-подписанных запросов, чтобы генерировать свежий nonce при retry.
    """
    _validate_outbound_url(url)
    for attempt in range(API_RETRY_ATTEMPTS):
        effective_headers = make_headers() if make_headers is not None else headers
        body = _json_body(data)
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in (effective_headers or {}).items():
            req.add_header(k, v)
        try:
            with _safe_open(req, timeout=10) as r:
                _observe_http_request("POST", url, r.status)
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode() if e.fp else ""
            if _should_retry_status(e.code) and attempt < API_RETRY_ATTEMPTS - 1:
                delay = _retry_delay_sec(attempt)
                _observe_http_retry("POST", url, f"http_{e.code}")
                logger.warning("API POST retry after status=%s in %.2fs", e.code, delay)
                time.sleep(delay)
                continue
            parsed_body = None
            if err_body:
                try:
                    parsed = json.loads(err_body)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    parsed_body = parsed
            logger.warning("HTTP POST failed: status=%s detail=%s", e.code, _http_error_log_detail(e))
            _observe_http_request("POST", url, e.code)
            return e.code, parsed_body
        except Exception as e:
            if attempt < API_RETRY_ATTEMPTS - 1:
                delay = _retry_delay_sec(attempt)
                _observe_http_retry("POST", url, "network_error")
                logger.warning(
                    "API POST retry after network error (%s) in %.2fs",
                    _exception_name(e),
                    delay,
                )
                time.sleep(delay)
                continue
            logger.debug("HTTP POST failed: %s", _exception_name(e))
            _observe_http_request("POST", url, 0)
            return 0, None
    _observe_http_request("POST", url, 0)
    return 0, None


def _http_put(
    url: str,
    data: dict,
    headers: dict[str, str] | None = None,
    *,
    make_headers: Callable[[], dict[str, str]] | None = None,
) -> int:
    """PUT JSON. Возвращает status_code."""
    _validate_outbound_url(url)
    for attempt in range(API_RETRY_ATTEMPTS):
        effective_headers = make_headers() if make_headers is not None else headers
        body = _json_body(data)
        req = urllib.request.Request(url, data=body, method="PUT")
        req.add_header("Content-Type", "application/json")
        for k, v in (effective_headers or {}).items():
            req.add_header(k, v)
        try:
            with _safe_open(req, timeout=10) as r:
                _observe_http_request("PUT", url, r.status)
                return r.status
        except urllib.error.HTTPError as e:
            if _should_retry_status(e.code) and attempt < API_RETRY_ATTEMPTS - 1:
                delay = _retry_delay_sec(attempt)
                _observe_http_retry("PUT", url, f"http_{e.code}")
                logger.warning("API PUT retry after status=%s in %.2fs", e.code, delay)
                time.sleep(delay)
                continue
            _observe_http_request("PUT", url, e.code)
            return e.code
        except Exception as e:
            if attempt < API_RETRY_ATTEMPTS - 1:
                delay = _retry_delay_sec(attempt)
                _observe_http_retry("PUT", url, "network_error")
                logger.warning(
                    "API PUT retry after network error (%s) in %.2fs",
                    _exception_name(e),
                    delay,
                )
                time.sleep(delay)
                continue
            logger.debug("HTTP PUT failed: %s", _exception_name(e))
            _observe_http_request("PUT", url, 0)
            return 0
    _observe_http_request("PUT", url, 0)
    return 0


def _http_get(url: str, params: dict, headers: dict[str, str] | None = None) -> dict | None:
    """GET с query params. Возвращает json или None."""
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}" if qs else url
    try:
        _validate_outbound_url(full_url)
        req = urllib.request.Request(full_url, method="GET")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with _safe_open(req, timeout=35) as r:
            _observe_http_request("GET", full_url, _response_status(r, 200))
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        logger.warning("HTTP GET failed: status=%s detail=%s", e.code, _http_error_log_detail(e))
        _observe_http_request("GET", full_url, e.code)
        return None
    except Exception as e:
        logger.warning("HTTP GET failed: %s", _exception_name(e))
        _observe_http_request("GET", full_url, 0)
        return None


def _sign_user_request(
    user_hmac_secret: str,
    method: str,
    path: str,
    telegram_id: int,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    payload = "\n".join([method.upper(), path.strip(), str(telegram_id), timestamp.strip(), nonce.strip(), body_hash])
    return hmac.new(user_hmac_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _signed_user_headers(
    api_auth_token: str,
    user_hmac_secret: str,
    method: str,
    url: str,
    telegram_id: int,
    body: bytes,
) -> dict[str, str]:
    path = urllib.parse.urlsplit(url).path or "/"
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = _sign_user_request(user_hmac_secret, method, path, telegram_id, timestamp, nonce, body)
    return {
        "Authorization": f"Bearer {api_auth_token}",
        "X-Telegram-ID": str(telegram_id),
        "X-Request-Timestamp": timestamp,
        "X-Request-Nonce": nonce,
        "X-Request-Signature": signature,
    }


def post_users(api_url: str, telegram_id: int, api_auth_token: str, api_user_hmac_secret: str) -> int | None:
    """POST /users → user_id. None при ошибке."""
    url = f"{api_url.rstrip('/')}/users"
    payload = {"telegram_id": telegram_id}
    status, data = _http_post(
        url,
        payload,
        make_headers=lambda: _signed_user_headers(
            api_auth_token, api_user_hmac_secret, "POST", url, telegram_id, _json_body(payload)
        ),
    )
    if status != 200 or data is None:
        err = "API не отвечает" if status == 0 else f"HTTP {status}"
        logger.warning("POST /users failed: %s (url=%s)", err, url)
        return None
    return data.get("user_id")


def put_user_profile(
    api_url: str,
    user_id: int,
    telegram_id: int,
    profile_text: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool:
    """PUT /users/:id/profile. True при успехе."""
    return put_user_profile_status(
        api_url,
        user_id,
        telegram_id,
        profile_text,
        api_auth_token,
        api_user_hmac_secret,
    ) == 204


def put_user_profile_status(
    api_url: str,
    user_id: int,
    telegram_id: int,
    profile_text: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> int:
    """PUT /users/:id/profile. Returns status code (204 on success)."""
    url = f"{api_url.rstrip('/')}/users/{user_id}/profile"
    payload = {"profile_text": profile_text}
    status = _http_put(
        url,
        payload,
        make_headers=lambda: _signed_user_headers(
            api_auth_token, api_user_hmac_secret, "PUT", url, telegram_id, _json_body(payload)
        ),
    )
    if status != 204:
        logger.warning("PUT /users/:id/profile failed: status=%s", status)
    return status


def put_user_notify_hour(
    api_url: str,
    user_id: int,
    telegram_id: int,
    hour: int,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> int:
    """PUT /users/:id/notify-hour. Возвращает status_code (204 — успех, 403 — не Pro)."""
    url = f"{api_url.rstrip('/')}/users/{user_id}/notify-hour"
    payload = {"hour": hour}
    return _http_put(
        url,
        payload,
        make_headers=lambda: _signed_user_headers(
            api_auth_token, api_user_hmac_secret, "PUT", url, telegram_id, _json_body(payload)
        ),
    )


def get_user_preferences(
    api_url: str,
    user_id: int,
    telegram_id: int,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> dict | None:
    """GET /users/:id/preferences. Возвращает json или None."""
    url = f"{api_url.rstrip('/')}/users/{user_id}/preferences"
    body = b""
    return _http_get(
        url,
        {},
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "GET", url, telegram_id, body),
    )


def get_user_is_pro(
    api_url: str,
    user_id: int,
    telegram_id: int,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool | None:
    """Return True/False when backend reports is_pro, else None."""
    prefs = get_user_preferences(api_url, user_id, telegram_id, api_auth_token, api_user_hmac_secret)
    if not isinstance(prefs, dict) or "is_pro" not in prefs:
        return None
    return bool(prefs.get("is_pro"))


def get_user_profile_text(
    api_url: str,
    user_id: int,
    telegram_id: int,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> str | None:
    """GET /users/:id — returns profile_text string or None on error."""
    url = f"{api_url.rstrip('/')}/users/{user_id}"
    data = _http_get(
        url,
        {},
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "GET", url, telegram_id, b""),
    )
    if not isinstance(data, dict):
        return None
    return data.get("profile_text") or ""


def get_user_stats(
    api_url: str,
    user_id: int,
    telegram_id: int,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> dict | None:
    """GET /users/:id/stats. Returns json payload or None."""
    url = f"{api_url.rstrip('/')}/users/{user_id}/stats"
    body = b""
    return _http_get(
        url,
        {},
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "GET", url, telegram_id, body),
    )


def _to_int_or_default(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cache_user_id(telegram_id: int, user_id: int, *, now_monotonic: float | None = None) -> None:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    if _STATE_STORE is not None:
        try:
            _STATE_STORE.cache_user_id(telegram_id, user_id)
        except Exception as e:
            logger.warning("redis state store cache_user_id failed: %s", _exception_name(e))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="cache_user_id", result="error")
    with _CACHE_LOCK:
        _prune_expired_entries(_USER_ID_CACHE, now)
        ttl_sec = getattr(_USER_ID_CACHE, "ttl_sec", USER_ID_CACHE_TTL_SEC)
        maxsize = getattr(_USER_ID_CACHE, "maxsize", USER_ID_CACHE_MAXSIZE)

        if telegram_id in _USER_ID_CACHE:
            _USER_ID_CACHE.pop(telegram_id, None)
        elif maxsize > 0 and len(_USER_ID_CACHE) >= maxsize:
            oldest_key = next(iter(_USER_ID_CACHE))
            _USER_ID_CACHE.pop(oldest_key, None)

        _USER_ID_CACHE[telegram_id] = (user_id, now + ttl_sec)
    _METRICS.inc("telegram_bot_cache_operations_total", cache="user_id", result="set")


def _get_cached_user_id(telegram_id: int, *, now_monotonic: float | None = None) -> int | None:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    with _CACHE_LOCK:
        _prune_expired_entries(_USER_ID_CACHE, now)
        cached = _USER_ID_CACHE.get(telegram_id)
        if cached is not None:
            user_id, expires_at = cached
            if now >= expires_at:
                _USER_ID_CACHE.pop(telegram_id, None)
                _METRICS.inc("telegram_bot_cache_operations_total", cache="user_id", result="expired")
            else:
                _METRICS.inc("telegram_bot_cache_operations_total", cache="user_id", result="hit")
                return user_id
        else:
            _METRICS.inc("telegram_bot_cache_operations_total", cache="user_id", result="miss")
    if _STATE_STORE is not None:
        try:
            stored_user_id = _STATE_STORE.get_user_id(telegram_id)
        except Exception as e:
            logger.warning("redis state store get_user_id failed: %s", _exception_name(e))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_user_id", result="error")
        else:
            if stored_user_id is not None:
                _cache_user_id(telegram_id, stored_user_id, now_monotonic=now)
                _METRICS.inc("telegram_bot_cache_operations_total", cache="user_id", result="redis_hit")
                return stored_user_id
            _METRICS.inc("telegram_bot_cache_operations_total", cache="user_id", result="redis_miss")
            return None
    return None


def _set_conversation_state(telegram_id: int, state: str, *, now_monotonic: float | None = None) -> None:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    if _STATE_STORE is not None:
        try:
            _STATE_STORE.set_conversation_state(telegram_id, state)
        except Exception as e:
            logger.warning("redis state store set_conversation_state failed: %s", _exception_name(e))
            _METRICS.inc(
                "telegram_bot_state_store_operations_total",
                operation="set_conversation_state",
                result="error",
            )
    with _CACHE_LOCK:
        _prune_expired_entries(_CONVERSATION_STATE_CACHE, now)
        ttl_sec = getattr(_CONVERSATION_STATE_CACHE, "ttl_sec", CONVERSATION_STATE_TTL_SEC)
        maxsize = getattr(_CONVERSATION_STATE_CACHE, "maxsize", CONVERSATION_STATE_MAXSIZE)
        if telegram_id in _CONVERSATION_STATE_CACHE:
            _CONVERSATION_STATE_CACHE.pop(telegram_id, None)
        elif maxsize > 0 and len(_CONVERSATION_STATE_CACHE) >= maxsize:
            oldest_key = next(iter(_CONVERSATION_STATE_CACHE))
            _CONVERSATION_STATE_CACHE.pop(oldest_key, None)
        _CONVERSATION_STATE_CACHE[telegram_id] = (state, now + ttl_sec)
    _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="set")


def _get_conversation_state(telegram_id: int, *, now_monotonic: float | None = None) -> str | None:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    with _CACHE_LOCK:
        _prune_expired_entries(_CONVERSATION_STATE_CACHE, now)
        cached = _CONVERSATION_STATE_CACHE.get(telegram_id)
        if cached is not None:
            state, expires_at = cached
            if now >= expires_at:
                _CONVERSATION_STATE_CACHE.pop(telegram_id, None)
                _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="expired")
            else:
                _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="hit")
                return state
        else:
            _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="miss")
    if _STATE_STORE is not None:
        try:
            stored_state = _STATE_STORE.get_conversation_state(telegram_id)
        except Exception as e:
            logger.warning("redis state store get_conversation_state failed: %s", _exception_name(e))
            _METRICS.inc(
                "telegram_bot_state_store_operations_total",
                operation="get_conversation_state",
                result="error",
            )
        else:
            if stored_state is not None:
                _set_conversation_state(telegram_id, stored_state, now_monotonic=now)
                _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="redis_hit")
                return stored_state
            _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="redis_miss")
            return None
    return None


def _clear_conversation_state(telegram_id: int) -> None:
    with _CACHE_LOCK:
        removed = _CONVERSATION_STATE_CACHE.pop(telegram_id, None)
    if _STATE_STORE is not None:
        try:
            _STATE_STORE.clear_conversation_state(telegram_id)
        except Exception as e:
            logger.warning("redis state store clear_conversation_state failed: %s", _exception_name(e))
            _METRICS.inc(
                "telegram_bot_state_store_operations_total",
                operation="clear_conversation_state",
                result="error",
            )
    if removed is not None:
        _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="clear")


def _get_batch_session(session_id: str) -> dict | None:
    session_key = (session_id or "").strip()
    if not session_key or _STATE_STORE is None:
        return None
    try:
        session = _STATE_STORE.get_batch_session(session_key)
    except Exception as e:
        logger.warning("redis state store get_batch_session failed: %s", _exception_name(e))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_batch_session", result="error")
        return None
    if not isinstance(session, dict):
        return None
    if "version" not in session:
        return None
    try:
        int(session.get("telegram_id"))
    except (TypeError, ValueError):
        return None
    if _batch_session_current_index(session) is None:
        return None
    return session


def _save_batch_session(session_id: str, session: dict) -> None:
    session_key = (session_id or "").strip()
    if not session_key or _STATE_STORE is None:
        return
    try:
        _STATE_STORE.set_batch_session(session_key, session)
    except Exception as e:
        logger.warning("redis state store set_batch_session failed: %s", _exception_name(e))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="set_batch_session", result="error")


def _clear_batch_session(session_id: str) -> None:
    session_key = (session_id or "").strip()
    if not session_key or _STATE_STORE is None:
        return
    try:
        _STATE_STORE.clear_batch_session(session_key)
    except Exception as e:
        logger.warning("redis state store clear_batch_session failed: %s", _exception_name(e))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="clear_batch_session", result="error")


def _batch_session_items(session: dict) -> list[dict] | None:
    items = session.get("items")
    if not isinstance(items, list) or not items:
        return None
    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        normalized.append(item)
    return normalized


def _batch_session_current_index(session: dict) -> int | None:
    try:
        current_index = int(session.get("current_index"))
    except (TypeError, ValueError):
        return None
    items = _batch_session_items(session)
    if items is None or current_index < 0 or current_index >= len(items):
        return None
    return current_index


def _format_unix_timestamp(raw_value: object) -> str | None:
    try:
        timestamp = int(raw_value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))


def _clean_job_text(text: str) -> str:
    """Strip HTML tags, markdown markers, and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_{1,2}(.*?)_{1,2}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _render_batch_card(session_id: str, session: dict, index: int | None = None) -> tuple[str, list[list[dict]]] | None:
    items = _batch_session_items(session)
    if items is None:
        return None
    if index is None:
        index = _batch_session_current_index(session)
    if index is None or index < 0 or index >= len(items):
        return None
    item = items[index]
    try:
        job_id = int(item.get("job_id"))
    except (TypeError, ValueError):
        return None
    if job_id <= 0:
        return None
    _TELEGRAM_MAX_MSG_LEN = 4096
    _DESC_MAX_LEN = 3200
    title = html.escape(_clean_job_text(str(item.get("title") or "Без названия")))
    raw_desc = _clean_job_text(str(item.get("description") or ""))
    if len(raw_desc) > _DESC_MAX_LEN:
        raw_desc = raw_desc[:_DESC_MAX_LEN] + "…"
    description = html.escape(raw_desc)
    budget = html.escape(_clean_job_text(str(item.get("budget") or "")))
    why_it_fits = html.escape(_clean_job_text(str(item.get("why_it_fits") or "")))
    score_percent = _to_int_or_default(item.get("score_percent"), 0)
    posted_at = _format_unix_timestamp(item.get("posted_at_unix"))
    created_at = _format_unix_timestamp(item.get("created_at_unix"))
    url = str(item.get("url") or "").strip()
    if not url:
        return None

    lines = [f"<b>{title}</b>"]
    if description:
        lines.append(description)
    meta: list[str] = []
    if budget:
        meta.append(f"Бюджет: {budget}")
    if score_percent > 0:
        meta.append(f"Совпадение: {score_percent}%")
    if posted_at:
        meta.append(f"Опубликовано: {posted_at}")
    if created_at:
        meta.append(f"Добавлено: {created_at}")
    if meta:
        lines.append("")
        lines.extend(meta)
    if why_it_fits:
        lines.append("")
        lines.append(f"Почему подходит: {why_it_fits}")

    nav_row: list[dict] = []
    if index > 0:
        nav_row.append({"text": "◀️", "callback_data": f"nav:p:{session_id}:{index - 1}"})
    nav_row.append({"text": f"{index + 1}/{len(items)}", "callback_data": f"nav:i:{session_id}:{index}"})
    if index + 1 < len(items):
        nav_row.append({"text": "▶️", "callback_data": f"nav:n:{session_id}:{index + 1}"})

    keyboard: list[list[dict]] = [nav_row]
    keyboard.append(
        [
            {"text": "👍", "callback_data": f"fb:g:{session_id}:{index}:{job_id}"},
            {"text": "👎", "callback_data": f"fb:b:{session_id}:{index}:{job_id}"},
        ]
    )
    keyboard.append([{"text": "Открыть проект", "url": url}])
    return "\n".join(lines), keyboard


def _render_batch_completion_text() -> str:
    return "Подборка завершена. Спасибо за обратную связь."


def _mark_first_seen(telegram_id: int) -> bool:
    """Returns True if user is new (first /start ever)."""
    if _STATE_STORE is None:
        return True
    try:
        return _STATE_STORE.set_first_seen_if_absent(telegram_id)
    except Exception as e:
        logger.warning("redis mark_first_seen failed: %s", _exception_name(e))
        return True


def _get_first_seen_ts(telegram_id: int) -> int | None:
    if _STATE_STORE is None:
        return None
    try:
        return _STATE_STORE.get_first_seen(telegram_id)
    except Exception as e:
        logger.warning("redis get_first_seen failed: %s", _exception_name(e))
        return None


def _set_processed_update_state(update_id: int, state: str, *, now_monotonic: float) -> None:
    with _CACHE_LOCK:
        _prune_expired_entries(_PROCESSED_UPDATE_CACHE, now_monotonic)
        maxsize = getattr(_PROCESSED_UPDATE_CACHE, "maxsize", PROCESSED_UPDATE_MAXSIZE)
        ttl_sec = getattr(_PROCESSED_UPDATE_CACHE, "ttl_sec", PROCESSED_UPDATE_TTL_SEC)
        if update_id in _PROCESSED_UPDATE_CACHE:
            _PROCESSED_UPDATE_CACHE.pop(update_id, None)
        elif maxsize > 0 and len(_PROCESSED_UPDATE_CACHE) >= maxsize:
            oldest_key = next(iter(_PROCESSED_UPDATE_CACHE))
            _PROCESSED_UPDATE_CACHE.pop(oldest_key, None)
        _PROCESSED_UPDATE_CACHE[update_id] = (state, now_monotonic + ttl_sec)


def _claim_update_id(update_id: int, *, now_monotonic: float | None = None) -> str:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    if _STATE_STORE is not None:
        try:
            result = _STATE_STORE.claim_update_id(update_id)
        except Exception as e:
            logger.warning("redis state store claim_update_id failed: %s", _exception_name(e))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="claim_update_id", result="error")
        else:
            cache_state = _UPDATE_INFLIGHT if result == _UPDATE_CLAIMED else result
            _set_processed_update_state(update_id, cache_state, now_monotonic=now)
            _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result=f"redis_{result}")
            return result
    with _CACHE_LOCK:
        _prune_expired_entries(_PROCESSED_UPDATE_CACHE, now)
        cached = _PROCESSED_UPDATE_CACHE.get(update_id)
        if cached is not None:
            state, expires_at = cached
            if now < expires_at:
                _METRICS.inc(
                    "telegram_bot_cache_operations_total",
                    cache="processed_update",
                    result=f"duplicate_{state}",
                )
                return state
    _set_processed_update_state(update_id, _UPDATE_INFLIGHT, now_monotonic=now)
    _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result="claim")
    return _UPDATE_CLAIMED


def _complete_update_id(update_id: int, *, now_monotonic: float | None = None) -> None:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    _set_processed_update_state(update_id, _UPDATE_DONE, now_monotonic=now)
    if _STATE_STORE is not None:
        try:
            _STATE_STORE.complete_update_id(update_id)
        except Exception as e:
            logger.warning("redis state store complete_update_id failed: %s", _exception_name(e))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="complete_update_id", result="error")
    _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result="complete")


def _forget_update_id(update_id: int) -> None:
    with _CACHE_LOCK:
        removed = _PROCESSED_UPDATE_CACHE.pop(update_id, None)
    if _STATE_STORE is not None:
        try:
            _STATE_STORE.forget_update_id(update_id)
        except Exception as e:
            logger.warning("redis state store forget_update_id failed: %s", _exception_name(e))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="forget_update_id", result="error")
    if removed is not None:
        _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result="forget")


def _resolve_user_id(api_url: str, telegram_id: int, api_auth_token: str, api_user_hmac_secret: str) -> int | None:
    cached_user_id = _get_cached_user_id(telegram_id)
    if cached_user_id is not None:
        return cached_user_id
    user_id = post_users(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
    if user_id is not None:
        _cache_user_id(telegram_id, user_id)
    return user_id


def set_my_commands(token: str) -> bool:
    """Зарегистрировать меню команд бота через setMyCommands."""
    url = f"{TELEGRAM_BASE}{token}/setMyCommands"
    commands = [
        {"command": "start",       "description": "Начать / перезапустить настройку профиля"},
        {"command": "profile",     "description": "Обновить профиль фрилансера вручную"},
        {"command": "stats",       "description": "Показать статистику подбора за 7 дней"},
        {"command": "notify_hour", "description": "Установить час дайджеста (Pro, 0–23, МСК)"},
        {"command": "help",        "description": "Список команд"},
    ]
    status, _ = _http_post(url, {"commands": commands})
    if status != 200:
        logger.warning("setMyCommands failed: status=%s", status)
        return False
    logger.info("bot commands registered")
    return True


def set_webhook(token: str, url: str, secret_token: str, max_connections: int = 40) -> bool:
    """Register Telegram webhook."""
    api_url = f"{TELEGRAM_BASE}{token}/setWebhook"
    status, data = _http_post(
        api_url,
        {
            "url": url,
            "secret_token": secret_token,
            "max_connections": max_connections,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
        },
    )
    if status != 200:
        logger.error("setWebhook failed: status=%s response=%r", status, data)
        return False
    if not isinstance(data, dict) or data.get("ok") is not True:
        logger.error("setWebhook failed: unexpected response=%r", data)
        return False
    logger.info("webhook registered: %s", url)
    return True


def delete_webhook(token: str, drop_pending: bool = False) -> None:
    """Delete Telegram webhook."""
    api_url = f"{TELEGRAM_BASE}{token}/deleteWebhook"
    status, _ = _http_post(api_url, {"drop_pending_updates": drop_pending})
    if status != 200:
        logger.warning("deleteWebhook failed: status=%s", status)
        return
    logger.info("webhook deleted")


def get_webhook_info(token: str) -> dict:
    """Return Telegram webhook metadata for the current bot token."""
    data = _http_get(f"{TELEGRAM_BASE}{token}/getWebhookInfo", {})
    if not data or not data.get("ok"):
        return {}
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def _resolve_bot_token(app_env: str) -> tuple[str, str]:
    """Resolve bot token, preferring a local override outside production."""
    if not _is_production_env(app_env):
        local_token = (os.getenv(LOCAL_BOT_TOKEN_ENV) or "").strip()
        if local_token:
            return local_token, LOCAL_BOT_TOKEN_ENV
    return (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(), "TELEGRAM_BOT_TOKEN"


def _polling_active_webhook_policy() -> str:
    policy = (os.getenv(POLLING_ACTIVE_WEBHOOK_POLICY_ENV, DEFAULT_POLLING_ACTIVE_WEBHOOK_POLICY) or "").strip().lower()
    if not policy:
        return DEFAULT_POLLING_ACTIVE_WEBHOOK_POLICY
    return policy


def run_polling_standby(reason: str, *, sleep_sec: float = POLLING_STANDBY_SLEEP_SEC) -> None:
    """Keep the local bot healthy without polling when another webhook owns the token."""
    heartbeat_file = os.getenv(HEARTBEAT_FILE_ENV, DEFAULT_HEARTBEAT_FILE)
    stop_event = threading.Event()

    def _handle_shutdown(signum: int, frame: object) -> None:
        _ = frame
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        logger.info("standby shutdown signal received: %s", signal_name)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    logger.warning("polling standby enabled: %s", _compact_log_text(reason, 240))
    while not stop_event.is_set():
        _touch_heartbeat(heartbeat_file)
        stop_event.wait(timeout=max(1.0, float(sleep_sec)))
    logger.info("polling standby stopped")


def send_message(token: str, chat_id: int, text: str, *, parse_html: bool = False) -> bool:
    """Отправить сообщение в чат."""
    url = f"{TELEGRAM_BASE}{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_html:
        payload["parse_mode"] = "HTML"
    status, data = _http_post(url, payload)
    if status != 200:
        detail = ""
        if isinstance(data, dict):
            description = data.get("description")
            if isinstance(description, str) and description.strip():
                detail = f" detail={_compact_log_text(description)}"
        logger.warning("sendMessage failed: status=%s%s", status, detail)
        return False
    return True


def post_feedback(
    api_url: str,
    user_id: int,
    telegram_id: int,
    job_id: int,
    feedback: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool:
    """POST /users/{user_id}/feedback — record 👍/👎 for a job."""
    url = f"{api_url.rstrip('/')}/users/{user_id}/feedback"
    payload = {"job_id": job_id, "feedback": feedback}
    status, _ = _http_post(
        url,
        payload,
        make_headers=lambda: _signed_user_headers(
            api_auth_token, api_user_hmac_secret, "POST", url, telegram_id, _json_body(payload)
        ),
    )
    if status not in (200, 204):
        logger.warning("POST /users/:id/feedback failed: status=%s", status)
        return False
    return True


def answer_callback_query(
    token: str,
    callback_query_id: str,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    """Send answerCallbackQuery to dismiss loading state in Telegram."""
    url = f"{TELEGRAM_BASE}{token}/answerCallbackQuery"
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    _http_post(url, payload)


# ---------------------------------------------------------------------------
# Onboarding wizard — keyboard helpers and handlers
# ---------------------------------------------------------------------------

def send_keyboard(token: str, chat_id: int, text: str, keyboard: list[list[dict]]) -> int | None:
    """sendMessage with inline_keyboard. Returns message_id or None on failure."""
    url = f"{TELEGRAM_BASE}{token}/sendMessage"
    status, data = _http_post(url, {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": keyboard},
    })
    if status != 200 or data is None:
        detail = ""
        if isinstance(data, dict):
            description = data.get("description")
            if isinstance(description, str) and description.strip():
                detail = f" detail={_compact_log_text(description)}"
        logger.warning("sendMessage(keyboard) failed: status=%s%s", status, detail)
        return None
    return (data.get("result") or {}).get("message_id")


def edit_message_text(
    token: str,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: list[list[dict]] | None = None,
) -> None:
    """editMessageText — replace text (and optionally keyboard) of an existing message."""
    url = f"{TELEGRAM_BASE}{token}/editMessageText"
    payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    status, data = _http_post(url, payload)
    if status != 200:
        detail = ""
        if isinstance(data, dict):
            description = data.get("description")
            if isinstance(description, str) and description.strip():
                detail = f" detail={_compact_log_text(description)}"
        logger.warning("editMessageText failed: status=%s%s", status, detail)


def _build_category_keyboard() -> list[list[dict]]:
    cats = list(ONBOARDING_CATEGORIES.items())
    rows: list[list[dict]] = []
    for i in range(0, len(cats), 2):
        rows.append([{"text": label, "callback_data": f"ob:cat:{key}"} for key, label in cats[i:i + 2]])
    return rows


def _build_skills_keyboard(cat: str, selected: list[str]) -> list[list[dict]]:
    skills = ONBOARDING_SKILLS.get(cat, [])
    sel = set(selected)
    rows: list[list[dict]] = []
    for i in range(0, len(skills), 2):
        rows.append([
            {"text": ("✅ " if s in sel else "◻️ ") + s, "callback_data": f"ob:skl:{s}"}
            for s in skills[i:i + 2]
        ])
    rows.append([{"text": "✓ Готово", "callback_data": "ob:skldone"}])
    return rows


def _build_experience_keyboard() -> list[list[dict]]:
    exps = list(ONBOARDING_EXPERIENCE.items())
    rows: list[list[dict]] = []
    for i in range(0, len(exps), 2):
        rows.append([{"text": label, "callback_data": f"ob:exp:{key}"} for key, label in exps[i:i + 2]])
    return rows


def _build_rate_keyboard() -> list[list[dict]]:
    return [
        [
            {"text": ONBOARDING_RATE["low"],  "callback_data": "ob:rate:low"},
            {"text": ONBOARDING_RATE["mid"],  "callback_data": "ob:rate:mid"},
        ],
        [
            {"text": ONBOARDING_RATE["high"], "callback_data": "ob:rate:high"},
            {"text": ONBOARDING_RATE["top"],  "callback_data": "ob:rate:top"},
        ],
        [{"text": ONBOARDING_RATE["skip"], "callback_data": "ob:rate:skip"}],
    ]


def _parse_onboarding_state(raw: str | None) -> dict | None:
    """Return decoded onboarding state dict, or None if raw is not onboarding JSON."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "step" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _build_onboarding_profile_text(state: dict) -> str:
    """Build profile text from onboarding answers.

    Must produce >= 50 runes to pass backend minProfileTextLen validation.
    Uses verbose format ("Специализация: ..., Уровень: ..., Навыки: ..., Ставка: ...")
    so even the minimal selection (category + exp only) clears the 50-rune threshold.
    """
    cat = state.get("cat", "other")
    cat_label = ONBOARDING_CATEGORIES.get(cat, cat)
    skills: list[str] = state.get("skills") or []
    exp = state.get("exp")
    rate = state.get("rate")
    exp_label = ONBOARDING_EXPERIENCE.get(exp, "") if exp else ""
    rate_label = ONBOARDING_RATE.get(rate, "") if rate and rate != "skip" else ""

    parts: list[str] = [f"Специализация: {cat_label}."]
    if exp_label:
        parts.append(f"Уровень опыта: {exp_label}.")
    if skills:
        parts.append(f"Навыки: {', '.join(skills)}.")
    if rate_label:
        parts.append(f"Желаемая ставка: {rate_label}.")
    return " ".join(parts)


def _onboarding_start(token: str, chat_id: int, telegram_id: int) -> None:
    """Send the first step of the onboarding wizard (category selection)."""
    _set_conversation_state(telegram_id, json.dumps({"step": "category"}))
    send_keyboard(
        token, chat_id,
        "Добро пожаловать! Я подбираю фриланс-заказы под ваш профиль.\n\n"
        "Как это работает:\n"
        "1. Вы отвечаете на 4 коротких вопроса.\n"
        "2. Я собираю профиль и начинаю искать подходящие проекты.\n"
        "3. Вы ставите 👍/👎 под заказами, и подбор становится точнее.\n\n"
        "<b>Шаг 1 из 4.</b> Выберите специализацию:",
        _build_category_keyboard(),
    )


def _build_returning_user_keyboard() -> list[list[dict]]:
    return [
        [{"text": "🔄 Пройти анкету", "callback_data": "ob:restart"}],
        [{"text": "✏️ Обновить вручную", "callback_data": "ob:manual"}],
        [{"text": "Оставить текущий профиль", "callback_data": "ob:keep"}],
    ]


def _build_main_reply_keyboard() -> dict:
    """Persistent Reply Keyboard shown to registered users."""
    return {
        "keyboard": [
            [{"text": "👤 Мой профиль"}, {"text": "📊 Статистика"}],
            [{"text": "✏️ Обновить профиль"}, {"text": "⚙️ Настройки"}],
            [{"text": "❓ Помощь"}, {"text": "🔼 Скрыть меню"}],
        ],
        "resize_keyboard": True,
        "persistent": True,
        "is_persistent": True,
    }


def send_with_reply_keyboard(token: str, chat_id: int, text: str, *, parse_html: bool = False) -> None:
    """sendMessage with the main Reply Keyboard attached."""
    url = f"{TELEGRAM_BASE}{token}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": _build_main_reply_keyboard(),
    }
    if parse_html:
        payload["parse_mode"] = "HTML"
    status, data = _http_post(url, payload)
    if status != 200:
        detail = ""
        if isinstance(data, dict):
            description = data.get("description") or ""
            if isinstance(description, str) and description.strip():
                detail = f" detail={_compact_log_text(description)}"
        logger.warning("send_with_reply_keyboard failed: status=%s%s", status, detail)


def send_remove_keyboard(token: str, chat_id: int) -> None:
    """Send ReplyKeyboardRemove to hide the persistent keyboard."""
    url = f"{TELEGRAM_BASE}{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "Меню скрыто. Чтобы вернуть — отправьте /start.",
        "reply_markup": {"remove_keyboard": True},
    }
    status, _ = _http_post(url, payload)
    if status != 200:
        logger.warning("send_remove_keyboard failed: status=%s", status)


def _maybe_send_profile_quality_hint(token: str, chat_id: int, profile_text: str) -> None:
    if len(profile_text) < _ONBOARDING_MIN_PROFILE_LEN:
        send_keyboard(
            token, chat_id,
            "💡 Профиль получился коротким — это снижает точность подбора заказов. "
            "Хотите пройти анкету и добавить детали?",
            [[{"text": "🔄 Обновить через анкету", "callback_data": "ob:restart"}]],
        )


def _onboarding_handle_callback(
    data: str,
    token: str,
    chat_id: int,
    message_id: int,
    telegram_id: int,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> None:
    """Dispatch ob:* callback_data through the onboarding state machine."""
    raw_state = _get_conversation_state(telegram_id)
    ob = _parse_onboarding_state(raw_state) or {}

    if data.startswith("ob:cat:"):
        cat = data[len("ob:cat:"):]
        if cat not in ONBOARDING_CATEGORIES:
            return
        if ONBOARDING_SKILLS.get(cat):
            new_ob: dict = {"step": "skills", "cat": cat, "skills": []}
            _set_conversation_state(telegram_id, json.dumps(new_ob))
            edit_message_text(
                token, chat_id, message_id,
                "<b>Шаг 2 из 4.</b> Выберите навыки (можно несколько):",
                _build_skills_keyboard(cat, []),
            )
        else:
            new_ob = {"step": "experience", "cat": cat, "skills": []}
            _set_conversation_state(telegram_id, json.dumps(new_ob))
            edit_message_text(
                token, chat_id, message_id,
                "<b>Шаг 3 из 4.</b> Выберите уровень опыта:",
                _build_experience_keyboard(),
            )

    elif data.startswith("ob:skl:"):
        skill = data[len("ob:skl:"):]
        if ob.get("step") != "skills":
            return
        skills: list[str] = list(ob.get("skills") or [])
        if skill in skills:
            skills.remove(skill)
        else:
            skills.append(skill)
        ob["skills"] = skills
        _set_conversation_state(telegram_id, json.dumps(ob))
        edit_message_text(
            token, chat_id, message_id,
            "<b>Шаг 2 из 4.</b> Выберите навыки (можно несколько):",
            _build_skills_keyboard(ob.get("cat", ""), skills),
        )

    elif data == "ob:skldone":
        if ob.get("step") != "skills":
            return
        ob["step"] = "experience"
        _set_conversation_state(telegram_id, json.dumps(ob))
        edit_message_text(
            token, chat_id, message_id,
            "<b>Шаг 3 из 4.</b> Выберите уровень опыта:",
            _build_experience_keyboard(),
        )

    elif data.startswith("ob:exp:"):
        exp = data[len("ob:exp:"):]
        if exp not in ONBOARDING_EXPERIENCE:
            return
        ob["step"] = "rate"
        ob["exp"] = exp
        _set_conversation_state(telegram_id, json.dumps(ob))
        edit_message_text(
            token, chat_id, message_id,
            "<b>Шаг 4 из 4.</b> Укажите желаемую ставку:",
            _build_rate_keyboard(),
        )

    elif data.startswith("ob:rate:"):
        rate = data[len("ob:rate:"):]
        if rate not in ONBOARDING_RATE:
            return
        ob["step"] = "confirm"
        ob["rate"] = rate
        _set_conversation_state(telegram_id, json.dumps(ob))
        profile_text = _build_onboarding_profile_text(ob)
        edit_message_text(
            token, chat_id, message_id,
            f"Ваш профиль:\n\n<i>{html.escape(profile_text)}</i>\n\nСохранить или написать свой текст?",
            [
                [{"text": "✅ Сохранить", "callback_data": "ob:confirm"}],
                [{"text": "✏️ Написать вручную", "callback_data": "ob:edit"}],
            ],
        )

    elif data == "ob:confirm":
        ob = _parse_onboarding_state(_get_conversation_state(telegram_id)) or ob
        profile_text = _build_onboarding_profile_text(ob)
        user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is None:
            _record_command("onboarding", "resolve_failed")
            send_message(token, chat_id, _profile_missing_start_or_backend_message())
            return
        status = put_user_profile_status(
            api_url,
            user_id,
            telegram_id,
            profile_text,
            api_auth_token,
            api_user_hmac_secret,
        )
        if status == 204:
            _clear_conversation_state(telegram_id)
            _record_command("onboarding", "ok")
            edit_message_text(token, chat_id, message_id, f"✅ Профиль сохранён:\n\n<i>{html.escape(profile_text)}</i>")
            send_with_reply_keyboard(
                token,
                chat_id,
                "Как только появятся подходящие заказы — уведомлю вас.\n"
                "Нажимайте 👍/👎 под заказами, чтобы обучить алгоритм.",
            )
            _maybe_send_profile_quality_hint(token, chat_id, profile_text)
        else:
            if status == 400:
                _record_command("onboarding", "invalid_profile")
                send_message(
                    token,
                    chat_id,
                    "Профиль получился слишком коротким или похож на тестовую заглушку. "
                    "Добавьте стек, опыт и тип задач, которые вам интересны.",
                )
            else:
                if status == 0 or status >= 500:
                    _record_command("onboarding", "backend_unavailable")
                    send_message(token, chat_id, _profile_backend_unavailable_message())
                else:
                    _record_command("onboarding", "error")
                    send_message(token, chat_id, "Ошибка сохранения профиля. Попробуйте позже.")

    elif data == "ob:edit":
        ob["step"] = "edit"
        _set_conversation_state(telegram_id, json.dumps(ob))
        edit_message_text(
            token,
            chat_id,
            message_id,
            "✏️ Отправьте текст профиля следующим сообщением.\n\n"
            "Что лучше указать: стек, тип задач, опыт и какие проекты вам интересны.",
        )

    elif data == "ob:manual":
        _set_conversation_state(telegram_id, json.dumps({"step": "edit"}))
        edit_message_text(
            token,
            chat_id,
            message_id,
            "✏️ Отправьте текст профиля следующим сообщением.\n\n"
            "Что лучше указать: стек, тип задач, опыт и какие проекты вам интересны.",
        )

    elif data == "ob:restart":
        _clear_conversation_state(telegram_id)
        edit_message_text(token, chat_id, message_id, "🔄 Начинаем заново!")
        _onboarding_start(token, chat_id, telegram_id)

    elif data == "ob:keep":
        _clear_conversation_state(telegram_id)
        edit_message_text(token, chat_id, message_id, "Профиль не изменён.")


class _WebhookHandler(BaseHTTPRequestHandler):
    secret_token = ""
    bot_token = ""
    api_url = ""
    api_auth_token = ""
    api_user_hmac_secret = ""

    def do_POST(self) -> None:  # noqa: N802
        if self.path != WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        incoming_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(incoming_secret, self.secret_token):
            logger.warning("webhook: invalid secret from %s", self.client_address[0])
            self.send_response(403)
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        if length < 0:
            self.send_response(400)
            self.end_headers()
            return
        if length > WEBHOOK_MAX_BODY_BYTES:
            logger.warning(
                "webhook: rejecting oversized request body from %s: content_length=%d limit=%d",
                self.client_address[0],
                length,
                WEBHOOK_MAX_BODY_BYTES,
            )
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length)
        try:
            update = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        try:
            processed_ok = _enqueue_webhook_update(
                update,
                self.bot_token,
                self.api_url,
                self.api_auth_token,
                self.api_user_hmac_secret,
            )
        except Exception as e:
            logger.error("webhook enqueue failed: err=%s", _exception_name(e), exc_info=True)
            processed_ok = False
        self.send_response(200 if processed_ok else 500)
        self.end_headers()
        try:
            self.wfile.flush()
        except Exception:
            pass

    def log_message(self, fmt: str, *args) -> None:
        _ = fmt
        _ = args


def _menu_handle_callback(
    data: str,
    token: str,
    chat_id: int,
    telegram_id: int,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> None:
    """Handle menu: callback_data from Settings submenu."""
    if data == "menu:notify_hour":
        user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is None:
            send_message(token, chat_id, "Сначала отправьте /start")
            return
        is_pro = get_user_is_pro(api_url, user_id, telegram_id, api_auth_token, api_user_hmac_secret)
        if is_pro is False:
            send_message(token, chat_id, "⛔ Выбор часа доступен только Pro-пользователям.")
            return
        _set_conversation_state(telegram_id, "await_notify_hour")
        _record_command("notify_hour", "prompt")
        send_message(token, chat_id, "Отправьте следующим сообщением час от 0 до 23 (по МСК).")


def _parse_batch_nav_callback(data: str) -> tuple[str, str, int] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "nav" or parts[1] not in {"p", "i", "n"}:
        return None
    session_id = parts[2].strip()
    if not session_id:
        return None
    try:
        target_index = int(parts[3])
    except (TypeError, ValueError):
        return None
    if target_index < 0:
        return None
    return parts[1], session_id, target_index


def _parse_batch_feedback_callback(data: str) -> tuple[str, str, int, int] | None:
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "fb" or parts[1] not in {"g", "b"}:
        return None
    session_id = parts[2].strip()
    if not session_id:
        return None
    try:
        current_index = int(parts[3])
        job_id = int(parts[4])
    except (TypeError, ValueError):
        return None
    if current_index < 0 or job_id <= 0:
        return None
    return parts[1], session_id, current_index, job_id


def _stale_batch_session_text() -> str:
    """User-facing guidance when an interactive batch session has expired."""
    return "Подборка устарела. Обновите профиль: /profile"


def _handle_batch_nav_callback(
    data: str,
    token: str,
    chat_id: int,
    message_id: int,
    telegram_id: int,
    callback_id: str,
) -> None:
    parsed = _parse_batch_nav_callback(data)
    if parsed is None:
        return
    _nav_kind, session_id, target_index = parsed
    session = _get_batch_session(session_id)
    if session is None:
        answer_callback_query(
            token, callback_id,
            text=_stale_batch_session_text(),
            show_alert=True,
        )
        return
    try:
        session_telegram_id = int(session.get("telegram_id"))
    except (TypeError, ValueError):
        return
    if session_telegram_id != telegram_id:
        return
    items = _batch_session_items(session)
    if items is None or target_index >= len(items):
        return
    session["current_index"] = target_index
    _save_batch_session(session_id, session)
    rendered = _render_batch_card(session_id, session, target_index)
    if rendered is None:
        return
    text, keyboard = rendered
    edit_message_text(token, chat_id, message_id, text, keyboard)


def _handle_batch_feedback_callback(
    data: str,
    token: str,
    chat_id: int,
    message_id: int,
    telegram_id: int,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
    callback_id: str = "",
) -> bool:
    parsed = _parse_batch_feedback_callback(data)
    if parsed is None:
        return False
    fb_kind, session_id, current_index, job_id = parsed
    session = _get_batch_session(session_id)
    if session is None:
        if callback_id:
            answer_callback_query(
                token, callback_id,
                text=_stale_batch_session_text(),
                show_alert=True,
            )
        return True
    try:
        session_telegram_id = int(session.get("telegram_id"))
    except (TypeError, ValueError):
        return True
    if session_telegram_id != telegram_id:
        return True
    session_current_index = _batch_session_current_index(session)
    if session_current_index is None or session_current_index != current_index:
        return True
    item = _batch_session_items(session)
    if item is None or current_index >= len(item):
        return True
    current_item = item[current_index]
    try:
        current_job_id = int(current_item.get("job_id"))
    except (TypeError, ValueError):
        return True
    if current_job_id != job_id:
        return True

    feedback = "good" if fb_kind == "g" else "bad"

    # Advance card first so the UI updates immediately, before any slow API calls.
    next_index = current_index + 1
    if next_index < len(item):
        session["current_index"] = next_index
        _save_batch_session(session_id, session)
        rendered = _render_batch_card(session_id, session, next_index)
        if rendered is not None:
            text, keyboard = rendered
            edit_message_text(token, chat_id, message_id, text, keyboard)
    else:
        _clear_batch_session(session_id)
        edit_message_text(token, chat_id, message_id, _render_batch_completion_text(), [])

    # Dismiss the Telegram spinner before slow backend calls.
    if callback_id:
        try:
            answer_callback_query(token, callback_id)
        except Exception:
            pass

    # Post feedback after the card has already advanced.
    user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
    if user_id is not None:
        if not post_feedback(api_url, user_id, telegram_id, job_id, feedback, api_auth_token, api_user_hmac_secret):
            logger.warning("batch feedback failed: session_id=%s job_id=%d", session_id, job_id)
    else:
        logger.warning("batch feedback: could not resolve user_id for telegram_id=%s", telegram_id)

    return True


def handle_callback(
    callback: dict,
    token: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> None:
    """Handle inline keyboard callback (onboarding wizard + batch navigation + feedback)."""
    data = callback.get("data", "")
    callback_id = callback.get("id", "")
    from_user = callback.get("from", {})
    telegram_id = from_user.get("id")
    try:
        if data.startswith("ob:") and telegram_id is not None:
            msg = callback.get("message") or {}
            cb_chat_id = msg.get("chat", {}).get("id") or from_user.get("id")
            message_id = msg.get("message_id")
            if cb_chat_id and message_id:
                _onboarding_handle_callback(
                    data, token, cb_chat_id, message_id, telegram_id,
                    api_url, api_auth_token, api_user_hmac_secret,
                )
        elif data.startswith("nav:") and telegram_id is not None:
            msg = callback.get("message") or {}
            cb_chat_id = msg.get("chat", {}).get("id") or from_user.get("id")
            message_id = msg.get("message_id")
            if cb_chat_id and message_id:
                _handle_batch_nav_callback(data, token, cb_chat_id, message_id, telegram_id, callback_id)
        elif data.startswith("fb:") and telegram_id is not None:
            msg = callback.get("message") or {}
            cb_chat_id = msg.get("chat", {}).get("id") or from_user.get("id")
            message_id = msg.get("message_id")
            handled = False
            if cb_chat_id and message_id:
                handled = _handle_batch_feedback_callback(
                    data,
                    token,
                    cb_chat_id,
                    message_id,
                    telegram_id,
                    api_url,
                    api_auth_token,
                    api_user_hmac_secret,
                    callback_id,
                )
            if not handled:
                parts = data.split(":")
                if len(parts) == 3 and parts[1] in ("g", "b"):
                    feedback = "good" if parts[1] == "g" else "bad"
                    job_id = int(parts[2])
                    user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
                    if user_id is not None:
                        post_feedback(
                            api_url,
                            user_id,
                            telegram_id,
                            job_id,
                            feedback,
                            api_auth_token,
                            api_user_hmac_secret,
                        )
                        logger.info("feedback sent: job_id=%d feedback=%s", job_id, feedback)
                    else:
                        logger.warning("feedback: could not resolve user_id for telegram_id=%s", telegram_id)
                elif data.startswith("fb:"):
                    logger.warning("feedback: unknown callback_data format, ignoring: %s", data)
        elif data.startswith("menu:") and telegram_id is not None:
            msg = callback.get("message") or {}
            cb_chat_id = msg.get("chat", {}).get("id") or from_user.get("id")
            if cb_chat_id:
                _menu_handle_callback(
                    data, token, cb_chat_id, telegram_id,
                    api_url, api_auth_token, api_user_hmac_secret,
                )
    except Exception as e:
        logger.error("callback handling failed: %s", _exception_name(e))
    finally:
        # Always answer the callback to dismiss loading state in Telegram
        try:
            answer_callback_query(token, callback_id)
        except Exception:
            pass


def get_updates(
    token: str,
    offset: int | None,
    timeout: int = 30,
    *,
    include_status: bool = False,
) -> tuple[list[dict], int | None] | tuple[list[dict], int | None, bool]:
    """getUpdates. Возвращает (updates, next_offset) или (updates, next_offset, ok)."""
    url = f"{TELEGRAM_BASE}{token}/getUpdates"
    params: dict = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    data = _http_get(url, params)
    if data is None or not data.get("ok"):
        if include_status:
            return [], offset, False
        return [], offset
    updates = data.get("result", [])
    next_offset = (max(u["update_id"] for u in updates) + 1) if updates else offset
    if include_status:
        return updates, next_offset, True
    return updates, next_offset


def _extract_update_id(update: dict) -> int | None:
    raw = update.get("update_id")
    try:
        update_id = int(raw)
    except (TypeError, ValueError):
        return None
    if update_id <= 0:
        return None
    return update_id


def _profile_missing_start_or_backend_message() -> str:
    return (
        "Не удалось подготовить обновление профиля. "
        "Если вы ещё не регистрировались, отправьте /start. "
        "Если /start уже был, backend временно недоступен — попробуйте позже."
    )


def _profile_backend_unavailable_message() -> str:
    return (
        "Сервис профилей временно недоступен (backend не отвечает). "
        "Попробуйте обновить профиль через пару минут."
    )


def _profile_empty_message() -> str:
    return (
        "Пустой профиль не сохраню. Отправьте текст профиля одним сообщением "
        "или используйте /profile <текст профиля>."
    )


def _handle_profile_submission(
    token: str,
    chat_id: int,
    telegram_id: int,
    profile_text: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool:
    user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
    if user_id is None:
        _record_command("profile", "resolve_failed")
        send_message(token, chat_id, _profile_missing_start_or_backend_message())
        return True
    status = put_user_profile_status(api_url, user_id, telegram_id, profile_text, api_auth_token, api_user_hmac_secret)
    if status == 204:
        _clear_conversation_state(telegram_id)
        _record_command("profile", "ok")
        send_message(token, chat_id, "Профиль обновлён.")
        _maybe_send_profile_quality_hint(token, chat_id, profile_text)
    else:
        if status == 400:
            _record_command("profile", "invalid")
            send_message(
                token,
                chat_id,
                "Профиль слишком короткий или похож на тестовую заглушку. "
                "Опишите навыки, стек, опыт и типы задач, которые вам интересны.",
            )
        elif status == 0 or status >= 500:
            _record_command("profile", "backend_unavailable")
            send_message(token, chat_id, _profile_backend_unavailable_message())
        else:
            _record_command("profile", "error")
            send_message(token, chat_id, "Не удалось обновить профиль. Попробуйте позже.")
    return True


def _handle_notify_hour_submission(
    token: str,
    chat_id: int,
    telegram_id: int,
    raw_hour: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool:
    try:
        hour = int(raw_hour)
        if not (0 <= hour <= 23):
            raise ValueError("out of range")
    except (ValueError, TypeError):
        _record_command("notify_hour", "invalid")
        send_message(token, chat_id, "Укажите час от 0 до 23, например: /notify_hour 9")
        return True
    user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
    if user_id is None:
        _record_command("notify_hour", "missing_start")
        send_message(token, chat_id, "Сначала отправьте /start")
        return True
    status = put_user_notify_hour(api_url, user_id, telegram_id, hour, api_auth_token, api_user_hmac_secret)
    if status == 204:
        _clear_conversation_state(telegram_id)
        _record_command("notify_hour", "ok")
        send_message(token, chat_id, f"✅ Дайджест будет приходить в {hour:02d}:00 МСК.")
    elif status == 403:
        _clear_conversation_state(telegram_id)
        _record_command("notify_hour", "forbidden")
        send_message(token, chat_id, "⛔ Выбор часа доступен только Pro-пользователям.")
    else:
        _record_command("notify_hour", "error")
        send_message(token, chat_id, "Ошибка обновления. Попробуйте позже.")
    return True


def _handle_update(
    update: dict,
    token: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool:
    callback = update.get("callback_query")
    if callback:
        handle_callback(callback, token, api_url, api_auth_token, api_user_hmac_secret)
        return True

    msg = update.get("message")
    if not msg:
        return False
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    from_user = msg.get("from", {})
    telegram_id = from_user.get("id")
    # Translate Reply Keyboard button labels to equivalent commands
    _MENU_CMD_MAP = {"📊 Статистика": "/stats", "❓ Помощь": "/help"}
    if text in _MENU_CMD_MAP:
        text = _MENU_CMD_MAP[text]
    if chat_id is None or telegram_id is None:
        return False

    if text == "/start":
        _clear_conversation_state(telegram_id)
        user_id = post_users(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is not None:
            _cache_user_id(telegram_id, user_id)
            is_new = _mark_first_seen(telegram_id)
            if is_new:
                _record_command("start", "ok")
                _onboarding_start(token, chat_id, telegram_id)
            else:
                first_seen = _get_first_seen_ts(telegram_id)
                days_since = (time.time() - first_seen) / 86400 if first_seen else 0
                _record_command("start", "returning")
                if days_since >= 7:
                    send_keyboard(
                        token, chat_id,
                        "С возвращением! Хотите обновить профиль для лучшего подбора заказов?",
                        [
                            [{"text": "🔄 Пройти анкету", "callback_data": "ob:restart"}],
                            [{"text": "Оставить текущий профиль", "callback_data": "ob:keep"}],
                        ],
                    )
                    send_with_reply_keyboard(token, chat_id, "Меню доступно в любой момент 👇")
                else:
                    send_keyboard(
                        token,
                        chat_id,
                        "Вы уже зарегистрированы.\n\n"
                        "Можно заново пройти короткую анкету, обновить профиль вручную "
                        "или оставить текущий профиль без изменений.",
                        _build_returning_user_keyboard(),
                    )
                    send_with_reply_keyboard(token, chat_id, "Меню доступно в любой момент 👇")
        else:
            _record_command("start", "error")
            send_message(token, chat_id, "Ошибка регистрации. Попробуйте позже.")
        return True

    if text == "/profile" or text.startswith("/profile "):
        rest = text[len("/profile"):].strip()
        if not rest:
            _set_conversation_state(telegram_id, "await_profile")
            _record_command("profile", "prompt")
            send_message(
                token,
                chat_id,
                "Отправьте следующим сообщением текст профиля или используйте /profile <ваш текст профиля>",
            )
            return True
        return _handle_profile_submission(
            token,
            chat_id,
            telegram_id,
            rest,
            api_url,
            api_auth_token,
            api_user_hmac_secret,
        )

    if text == "/help" or text.startswith("/help@"):
        _clear_conversation_state(telegram_id)
        _record_command("help", "ok")
        send_with_reply_keyboard(
            token,
            chat_id,
            (
                "<b>Команды бота:</b>\n"
                "/start — зарегистрироваться\n"
                "/profile &lt;текст&gt; — обновить профиль фрилансера\n"
                "/stats — статистика подбора за последние 7 дней\n"
                "/notify_hour &lt;0–23&gt; — выбрать час дайджеста по МСК (только Pro)\n"
                "/help — эта справка\n\n"
                "После обновления профиля ИИ подберёт подходящие заказы и пришлёт уведомления.\n"
                "Нажмите 👍 или 👎 под каждым заказом, чтобы обучить алгоритм."
            ),
            parse_html=True,
        )
        return True

    if text == "/stats" or text.startswith("/stats@"):
        _clear_conversation_state(telegram_id)
        user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is None:
            _record_command("stats", "missing_start")
            send_message(token, chat_id, "Сначала отправьте /start")
            return True
        stats = get_user_stats(api_url, user_id, telegram_id, api_auth_token, api_user_hmac_secret)
        if not isinstance(stats, dict):
            _record_command("stats", "error")
            send_message(token, chat_id, "Не удалось получить статистику. Попробуйте позже.")
            return True

        period_days = max(1, _to_int_or_default(stats.get("period_days"), 7))
        projects_found = max(0, _to_int_or_default(stats.get("projects_found"), 0))
        projects_shown = max(0, _to_int_or_default(stats.get("projects_shown"), 0))
        filtered_other = max(0, _to_int_or_default(stats.get("projects_filtered_other"), 0))
        filtered_by_budget = max(0, _to_int_or_default(stats.get("projects_filtered_by_budget"), 0))
        budget_filter_active = bool(stats.get("budget_filter_active"))

        budget_line = (
            f"• Отфильтровано по бюджету: {filtered_by_budget}"
            if budget_filter_active
            else "• Бюджетный фильтр: не задан"
        )

        send_with_reply_keyboard(
            token,
            chat_id,
            (
                f"📊 Статистика за последние {period_days} дней:\n"
                f"• Найдено подходящих проектов: {projects_found}\n"
                f"• Показано вам: {projects_shown}\n"
                f"• Не показано после ранжирования/лимитов: {filtered_other}\n"
                f"{budget_line}"
            ),
        )
        _record_command("stats", "ok")
        return True

    if text == "/notify_hour" or text.startswith("/notify_hour "):
        rest = text[len("/notify_hour"):].strip()
        if not rest:
            user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
            if user_id is None:
                _record_command("notify_hour", "missing_start")
                send_message(token, chat_id, "Сначала отправьте /start")
                return True
            is_pro = get_user_is_pro(api_url, user_id, telegram_id, api_auth_token, api_user_hmac_secret)
            if is_pro is False:
                _clear_conversation_state(telegram_id)
                _record_command("notify_hour", "forbidden")
                send_message(token, chat_id, "⛔ Выбор часа доступен только Pro-пользователям.")
                return True
            _set_conversation_state(telegram_id, "await_notify_hour")
            _record_command("notify_hour", "prompt")
            send_message(
                token,
                chat_id,
                "Отправьте следующим сообщением час от 0 до 23 или используйте /notify_hour 9",
            )
            return True
        return _handle_notify_hour_submission(
            token,
            chat_id,
            telegram_id,
            rest,
            api_url,
            api_auth_token,
            api_user_hmac_secret,
        )

    if text == "👤 Мой профиль":
        _clear_conversation_state(telegram_id)
        user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is None:
            send_message(token, chat_id, "Сначала отправьте /start")
            return True
        profile_text = get_user_profile_text(api_url, user_id, telegram_id, api_auth_token, api_user_hmac_secret)
        if profile_text is None:
            send_message(token, chat_id, "Не удалось загрузить профиль. Попробуйте позже.")
        elif profile_text == "":
            send_message(token, chat_id, "Профиль не заполнен. Нажмите ✏️ Обновить профиль.")
        else:
            send_message(token, chat_id, f"👤 Ваш профиль:\n\n{profile_text}")
        return True

    if text == "✏️ Обновить профиль":
        _set_conversation_state(telegram_id, "await_profile")
        _record_command("profile", "prompt")
        send_message(token, chat_id, "Отправьте следующим сообщением текст профиля.")
        return True

    if text == "⚙️ Настройки":
        send_keyboard(
            token,
            chat_id,
            "⚙️ Настройки",
            [[{"text": "🕐 Час уведомлений", "callback_data": "menu:notify_hour"}]],
        )
        return True

    if text == "🔼 Скрыть меню":
        _clear_conversation_state(telegram_id)
        send_remove_keyboard(token, chat_id)
        return True

    pending_state = _get_conversation_state(telegram_id)
    ob_state = _parse_onboarding_state(pending_state)
    if pending_state == "await_profile" and not text:
        _record_command("profile", "empty")
        send_message(token, chat_id, _profile_empty_message())
        return True
    if ob_state is not None and ob_state.get("step") == "edit" and not text:
        _record_command("profile", "empty")
        send_message(token, chat_id, _profile_empty_message())
        return True
    if text and not text.startswith("/") and pending_state == "await_profile":
        return _handle_profile_submission(
            token,
            chat_id,
            telegram_id,
            text,
            api_url,
            api_auth_token,
            api_user_hmac_secret,
        )
    if text and not text.startswith("/") and ob_state is not None and ob_state.get("step") == "edit":
        return _handle_profile_submission(
            token,
            chat_id,
            telegram_id,
            text,
            api_url,
            api_auth_token,
            api_user_hmac_secret,
        )
    if text and not text.startswith("/") and pending_state == "await_notify_hour":
        return _handle_notify_hour_submission(
            token,
            chat_id,
            telegram_id,
            text,
            api_url,
            api_auth_token,
            api_user_hmac_secret,
        )

    return False


def _process_update(
    update: dict,
    transport: str,
    token: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
    *,
    claim_update_id: bool = True,
) -> bool:
    update_id = _extract_update_id(update)
    if claim_update_id and update_id is not None:
        claim_result = _claim_update_id(update_id)
        if claim_result == _UPDATE_DONE:
            logger.info("%s update skipped as duplicate: update_id=%s", transport, update_id)
            _METRICS.inc("telegram_bot_updates_total", transport=transport, result="duplicate")
            return True
        if claim_result == _UPDATE_INFLIGHT:
            logger.warning("%s update duplicate while in-flight: update_id=%s", transport, update_id)
            _METRICS.inc("telegram_bot_updates_total", transport=transport, result="inflight_duplicate")
            return False
    try:
        handled = _handle_update(update, token, api_url, api_auth_token, api_user_hmac_secret)
    except KeyboardInterrupt:
        if claim_update_id and update_id is not None:
            _forget_update_id(update_id)
        raise
    except Exception as e:
        if claim_update_id and update_id is not None:
            _forget_update_id(update_id)
        logger.error("%s update failed: update_id=%s err=%s", transport, update_id, _exception_name(e), exc_info=True)
        _METRICS.inc("telegram_bot_updates_total", transport=transport, result="failed")
        return False
    if claim_update_id and update_id is not None:
        _complete_update_id(update_id)
    _METRICS.inc("telegram_bot_updates_total", transport=transport, result="handled" if handled else "ignored")
    return True


def _enqueue_webhook_update(
    update: dict,
    token: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool:
    _ = token
    _ = api_url
    _ = api_auth_token
    _ = api_user_hmac_secret
    if _STATE_STORE is None:
        logger.error("webhook durable inbox unavailable: Redis state store is not configured")
        return False
    update_id = _extract_update_id(update)
    if update_id is None:
        logger.error("webhook durable inbox rejected update without update_id")
        return False
    try:
        payload = json.dumps(update, ensure_ascii=False, separators=(",", ":"))
        result = _STATE_STORE.enqueue_webhook_update(update_id, payload)
    except Exception as e:
        logger.error("webhook enqueue failed: update_id=%s err=%s", update_id, _exception_name(e), exc_info=True)
        return False
    _set_processed_update_state(
        update_id,
        _UPDATE_INFLIGHT if result == "enqueued" else _UPDATE_DONE,
        now_monotonic=time.monotonic(),
    )
    _METRICS.inc("telegram_bot_updates_total", transport="webhook", result=result)
    return result in {"enqueued", "duplicate"}


def _process_one_webhook_inbox_update(
    token: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
    *,
    timeout_sec: int = 1,
) -> bool:
    if _STATE_STORE is None:
        return False
    claimed = _STATE_STORE.claim_next_webhook_update(timeout_sec)
    if claimed is None:
        return False
    update_id, raw_payload = claimed
    try:
        update = json.loads(raw_payload)
    except json.JSONDecodeError:
        logger.error("webhook inbox payload invalid json: update_id=%s", update_id)
        _STATE_STORE.ack_webhook_update(update_id)
        _forget_update_id(update_id)
        return True

    processed_ok = _process_update(
        update,
        "webhook",
        token,
        api_url,
        api_auth_token,
        api_user_hmac_secret,
        claim_update_id=False,
    )
    if processed_ok:
        _complete_update_id(update_id)
        _STATE_STORE.ack_webhook_update(update_id)
    else:
        _STATE_STORE.requeue_webhook_update(update_id)
        _set_processed_update_state(update_id, _UPDATE_INFLIGHT, now_monotonic=time.monotonic())
    return True


def _run_webhook_inbox_worker(
    stop_event: threading.Event,
    token: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
    *,
    timeout_sec: int = 1,
) -> None:
    if _STATE_STORE is None:
        logger.error("webhook inbox worker unavailable: Redis state store is not configured")
        return
    recovered = _STATE_STORE.recover_webhook_processing()
    if recovered > 0:
        logger.warning("webhook inbox recovered stuck updates: count=%d", recovered)
    while not stop_event.is_set():
        try:
            handled = _process_one_webhook_inbox_update(
                token,
                api_url,
                api_auth_token,
                api_user_hmac_secret,
                timeout_sec=timeout_sec,
            )
        except Exception as e:
            logger.error("webhook inbox worker iteration failed: err=%s", _exception_name(e), exc_info=True)
            time.sleep(0.5)
            continue
        if not handled:
            stop_event.wait(timeout=max(0.1, float(timeout_sec)))


def run_polling(token: str, api_url: str, api_auth_token: str, api_user_hmac_secret: str) -> None:
    """Цикл getUpdates → обработка апдейтов."""
    offset: int | None = None
    poll_error_streak = 0
    heartbeat_file = os.getenv(HEARTBEAT_FILE_ENV, DEFAULT_HEARTBEAT_FILE)
    while True:
        _touch_heartbeat(heartbeat_file)
        result = get_updates(token, offset, include_status=True)
        poll_ok = True
        response_offset = offset
        if isinstance(result, tuple) and len(result) == 3:
            updates, response_offset, poll_ok = result
        else:
            updates, response_offset = result  # type: ignore[misc]
        if not poll_ok:
            delay = _poll_retry_delay_sec(poll_error_streak)
            logger.warning("getUpdates failed, retry in %.2fs", delay)
            time.sleep(delay)
            poll_error_streak += 1
            continue
        poll_error_streak = 0
        _touch_heartbeat(heartbeat_file)
        next_offset = response_offset if updates else offset
        for update in updates:
            update_id = _extract_update_id(update)
            if _process_update(update, "polling", token, api_url, api_auth_token, api_user_hmac_secret):
                if update_id is not None:
                    next_offset = update_id + 1
                continue
            logger.warning("update processing failed; preserving offset=%s for retry", offset)
            break
        else:
            offset = next_offset


def run_webhook(
    token: str,
    webhook_url: str,
    webhook_secret: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
    *,
    port: int = 8080,
    max_connections: int = 40,
    server_factory=ThreadingHTTPServer,
) -> None:
    _WebhookHandler.secret_token = webhook_secret
    _WebhookHandler.bot_token = token
    _WebhookHandler.api_url = api_url
    _WebhookHandler.api_auth_token = api_auth_token
    _WebhookHandler.api_user_hmac_secret = api_user_hmac_secret

    server = server_factory(("0.0.0.0", port), _WebhookHandler)
    logger.info("webhook server listening on port %d", port)
    heartbeat_file = os.getenv(HEARTBEAT_FILE_ENV, DEFAULT_HEARTBEAT_FILE)
    stop_event = threading.Event()

    try:
        heartbeat_max_age = float(os.getenv("TELEGRAM_HEARTBEAT_MAX_AGE_SEC", "90"))
    except ValueError:
        heartbeat_max_age = 90.0
    heartbeat_interval = max(1.0, min(30.0, heartbeat_max_age / 3.0))

    def _heartbeat_loop() -> None:
        while not stop_event.is_set():
            _touch_heartbeat(heartbeat_file)
            stop_event.wait(timeout=heartbeat_interval)

    def _handle_shutdown(signum: int, frame: object) -> None:
        _ = frame
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        logger.info("shutdown signal received: %s", signal_name)
        t = threading.Thread(target=server.shutdown, daemon=True, name="telegram-webhook-shutdown")
        t.start()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True, name="telegram-webhook-heartbeat")
    heartbeat_thread.start()
    worker_thread = threading.Thread(
        target=_run_webhook_inbox_worker,
        args=(stop_event, token, api_url, api_auth_token, api_user_hmac_secret),
        daemon=True,
        name="telegram-webhook-inbox-worker",
    )
    worker_thread.start()
    server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="telegram-webhook-server")
    server_thread.start()

    if not set_webhook(token, webhook_url, webhook_secret, max_connections=max_connections):
        logger.error("webhook registration failed; aborting")
        stop_event.set()
        try:
            server.shutdown()
        except Exception:
            pass
        server_thread.join(timeout=5)
        worker_thread.join(timeout=5)
        heartbeat_thread.join(timeout=5)
        try:
            server.server_close()
        except Exception:
            pass
        raise SystemExit(1)

    try:
        server_thread.join()
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=5)
        worker_thread.join(timeout=5)
        try:
            server.server_close()
        except Exception:
            pass
        logger.info("webhook server stopped")


def main() -> None:
    global _STATE_STORE
    _METRICS.set_ready(False)
    app_env = os.getenv("APP_ENV", "development")
    token, token_env_name = _resolve_bot_token(app_env)
    api_auth_token = os.getenv("API_AUTH_TOKEN")
    api_user_hmac_secret = os.getenv("API_USER_HMAC_SECRET")
    api_url = (os.getenv("API_URL") or "").strip()
    if not api_url and not _is_production_env(app_env):
        api_url = "http://localhost:8080"
    try:
        _validate_secret(token_env_name, token or "", 20)
        _validate_secret("API_AUTH_TOKEN", api_auth_token or "", 32)
        _validate_secret("API_USER_HMAC_SECRET", api_user_hmac_secret or "", 32)
        if _is_production_env(app_env):
            if not api_url:
                raise ValueError("API_URL not set in production")
            _validate_api_url_for_production(api_url)
        _register_allowed_host(api_url)
        _STATE_STORE = _build_state_store(app_env)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)
    bot_mode = (os.getenv("BOT_MODE", "polling") or "polling").strip().lower()
    if bot_mode not in {"polling", "webhook"}:
        logger.error("BOT_MODE must be polling or webhook")
        sys.exit(1)
    webhook_policy = _polling_active_webhook_policy()
    if webhook_policy not in {"standby", "ignore"}:
        logger.error("%s must be standby or ignore", POLLING_ACTIVE_WEBHOOK_POLICY_ENV)
        sys.exit(1)
    metrics_bind = os.getenv("BOT_METRICS_BIND", DEFAULT_METRICS_BIND)
    metrics_port = _read_non_negative_int_env("BOT_METRICS_PORT", DEFAULT_METRICS_PORT)
    metrics_server = _start_metrics_server(metrics_bind, metrics_port)
    logger.info("bot started, API=%s mode=%s token_env=%s", api_url, bot_mode, token_env_name)
    set_my_commands(token or "")
    _METRICS.set_ready(True)
    if bot_mode == "webhook":
        webhook_url = os.getenv("WEBHOOK_URL", "")
        webhook_secret = os.getenv("WEBHOOK_SECRET_TOKEN", "")
        port = _read_positive_int_env("PORT", 8080)
        max_connections = _read_bounded_int_env("WEBHOOK_MAX_CONNECTIONS", 40, min_value=1, max_value=100)
        try:
            _validate_webhook_url(webhook_url)
            _validate_secret("WEBHOOK_SECRET_TOKEN", webhook_secret, 32)
        except ValueError as e:
            logger.error("%s", e)
            sys.exit(1)
        run_webhook(
            token or "",
            webhook_url,
            webhook_secret,
            api_url,
            api_auth_token or "",
            api_user_hmac_secret or "",
            port=port,
            max_connections=max_connections,
        )
    else:
        try:
            if webhook_policy != "ignore":
                webhook_info = get_webhook_info(token or "")
                active_webhook_url = str(webhook_info.get("url") or "").strip()
                if active_webhook_url:
                    _METRICS.set_ready(False)
                    run_polling_standby(
                        f"active webhook detected for polling token: {active_webhook_url}",
                    )
                    return
            run_polling(token or "", api_url, api_auth_token or "", api_user_hmac_secret or "")
        finally:
            _METRICS.set_ready(False)
            if metrics_server is not None:
                metrics_server.shutdown()
                metrics_server.server_close()
        return
    _METRICS.set_ready(False)
    if metrics_server is not None:
        metrics_server.shutdown()
        metrics_server.server_close()


if __name__ == "__main__":
    main()
