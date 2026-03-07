#!/usr/bin/env python3
"""Telegram bot: getUpdates/webhook → backend API. Thin client with basic recovery and metrics."""

from __future__ import annotations

from collections import OrderedDict
import json
import logging
import math
import os
import random
import sys
import time
import secrets
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import signal
import threading
import urllib.request
import urllib.error
import urllib.parse

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
WEBHOOK_PATH = "/webhook"
DEFAULT_METRICS_BIND = "0.0.0.0"
DEFAULT_METRICS_PORT = 9107


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


class _ProcessedUpdateCache(OrderedDict[int, float]):
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
    "telegram_bot_state_store_operations_total": ("counter", "Telegram bot Redis state store operations by operation and result."),
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


def _prune_expired_entries(cache: OrderedDict[int, tuple[int, float]] | OrderedDict[int, tuple[str, float]] | OrderedDict[int, float], now: float) -> None:
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
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_conversation_state", result="invalid")
            return None
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_conversation_state", result="hit")
        return value

    def clear_conversation_state(self, telegram_id: int) -> None:
        self._client.delete(self._key("conversation", telegram_id))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="clear_conversation_state", result="ok")

    def claim_update_id(self, update_id: int) -> bool:
        claimed = bool(
            self._client.set(
                self._key("processed-update", update_id),
                "1",
                ex=self._processed_update_ttl_sec,
                nx=True,
            )
        )
        _METRICS.inc(
            "telegram_bot_state_store_operations_total",
            operation="claim_update_id",
            result="claim" if claimed else "duplicate",
        )
        return claimed

    def forget_update_id(self, update_id: int) -> None:
        self._client.delete(self._key("processed-update", update_id))
        _METRICS.inc("telegram_bot_state_store_operations_total", operation="forget_update_id", result="ok")

    def _key(self, kind: str, entity_id: int) -> str:
        return f"{self._prefix}:{kind}:{entity_id}"


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


_HTTP_ONLY_OPENER = urllib.request.build_opener(
    urllib.request.HTTPHandler(),
    urllib.request.HTTPSHandler(),
    _SafeRedirectHandler(),
)


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


def _http_post(url: str, data: dict, headers: dict[str, str] | None = None) -> tuple[int, dict | None]:
    """POST JSON. Возвращает (status_code, json_body или None)."""
    body = _json_body(data)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    _validate_outbound_url(url)
    for attempt in range(API_RETRY_ATTEMPTS):
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
            if e.code >= 500 and err_body:
                logger.warning("API error %s: %s", e.code, err_body[:200])
            _observe_http_request("POST", url, e.code)
            return e.code, None
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


def _http_put(url: str, data: dict, headers: dict[str, str] | None = None) -> int:
    """PUT JSON. Возвращает status_code."""
    body = _json_body(data)
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    _validate_outbound_url(url)
    for attempt in range(API_RETRY_ATTEMPTS):
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


def _http_get(url: str, params: dict) -> dict | None:
    """GET с query params. Возвращает json или None."""
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}" if qs else url
    try:
        _validate_outbound_url(full_url)
        with _safe_open(full_url, timeout=35) as r:
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
    body = _json_body(payload)
    status, data = _http_post(
        url,
        payload,
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "POST", url, telegram_id, body),
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
    url = f"{api_url.rstrip('/')}/users/{user_id}/profile"
    payload = {"profile_text": profile_text}
    body = _json_body(payload)
    status = _http_put(
        url,
        payload,
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "PUT", url, telegram_id, body),
    )
    if status != 204:
        logger.warning("PUT /users/:id/profile failed: status=%s", status)
        return False
    return True


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
    body = _json_body(payload)
    return _http_put(
        url,
        payload,
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "PUT", url, telegram_id, body),
    )


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
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="set_conversation_state", result="error")
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
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="get_conversation_state", result="error")
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
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="clear_conversation_state", result="error")
    if removed is not None:
        _METRICS.inc("telegram_bot_cache_operations_total", cache="conversation_state", result="clear")


def _claim_update_id(update_id: int, *, now_monotonic: float | None = None) -> bool:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    if _STATE_STORE is not None:
        try:
            claimed = _STATE_STORE.claim_update_id(update_id)
        except Exception as e:
            logger.warning("redis state store claim_update_id failed: %s", _exception_name(e))
            _METRICS.inc("telegram_bot_state_store_operations_total", operation="claim_update_id", result="error")
        else:
            if not claimed:
                _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result="redis_duplicate")
                return False
            with _CACHE_LOCK:
                _prune_expired_entries(_PROCESSED_UPDATE_CACHE, now)
                maxsize = getattr(_PROCESSED_UPDATE_CACHE, "maxsize", PROCESSED_UPDATE_MAXSIZE)
                ttl_sec = getattr(_PROCESSED_UPDATE_CACHE, "ttl_sec", PROCESSED_UPDATE_TTL_SEC)
                if update_id in _PROCESSED_UPDATE_CACHE:
                    _PROCESSED_UPDATE_CACHE.pop(update_id, None)
                elif maxsize > 0 and len(_PROCESSED_UPDATE_CACHE) >= maxsize:
                    oldest_key = next(iter(_PROCESSED_UPDATE_CACHE))
                    _PROCESSED_UPDATE_CACHE.pop(oldest_key, None)
                _PROCESSED_UPDATE_CACHE[update_id] = now + ttl_sec
            _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result="redis_claim")
            return True
    with _CACHE_LOCK:
        _prune_expired_entries(_PROCESSED_UPDATE_CACHE, now)
        expires_at = _PROCESSED_UPDATE_CACHE.get(update_id)
        if expires_at is not None and now < expires_at:
            _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result="duplicate")
            return False
        maxsize = getattr(_PROCESSED_UPDATE_CACHE, "maxsize", PROCESSED_UPDATE_MAXSIZE)
        ttl_sec = getattr(_PROCESSED_UPDATE_CACHE, "ttl_sec", PROCESSED_UPDATE_TTL_SEC)
        if update_id in _PROCESSED_UPDATE_CACHE:
            _PROCESSED_UPDATE_CACHE.pop(update_id, None)
        elif maxsize > 0 and len(_PROCESSED_UPDATE_CACHE) >= maxsize:
            oldest_key = next(iter(_PROCESSED_UPDATE_CACHE))
            _PROCESSED_UPDATE_CACHE.pop(oldest_key, None)
        _PROCESSED_UPDATE_CACHE[update_id] = now + ttl_sec
    _METRICS.inc("telegram_bot_cache_operations_total", cache="processed_update", result="claim")
    return True


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
        {"command": "start",       "description": "Начать работу с ботом"},
        {"command": "help",        "description": "Список команд"},
        {"command": "profile",     "description": "Обновить профиль фрилансера"},
        {"command": "notify_hour", "description": "Установить час дайджеста (Pro, 0–23, МСК)"},
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
    status, _ = _http_post(
        api_url,
        {
            "url": url,
            "secret_token": secret_token,
            "max_connections": max_connections,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        },
    )
    if status != 200:
        logger.error("setWebhook failed: status=%s", status)
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


def send_message(token: str, chat_id: int, text: str) -> bool:
    """Отправить сообщение в чат."""
    url = f"{TELEGRAM_BASE}{token}/sendMessage"
    status, _ = _http_post(url, {"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    if status != 200:
        logger.warning("sendMessage failed: status=%s", status)
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
    body = _json_body(payload)
    status, _ = _http_post(
        url,
        payload,
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "POST", url, telegram_id, body),
    )
    if status not in (200, 204):
        logger.warning("POST /users/:id/feedback failed: status=%s", status)
        return False
    return True


def answer_callback_query(token: str, callback_query_id: str) -> None:
    """Send answerCallbackQuery to dismiss loading state in Telegram."""
    url = f"{TELEGRAM_BASE}{token}/answerCallbackQuery"
    _http_post(url, {"callback_query_id": callback_query_id})


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
        body = self.rfile.read(length)
        try:
            update = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.flush()
        except Exception:
            pass

        t = threading.Thread(
            target=self._process_update,
            args=(update,),
            daemon=True,
            name="telegram-webhook-update",
        )
        t.start()

    def _process_update(self, update: dict) -> None:
        _process_update(
            update,
            "webhook",
            self.bot_token,
            self.api_url,
            self.api_auth_token,
            self.api_user_hmac_secret,
        )

    def log_message(self, fmt: str, *args) -> None:
        _ = fmt
        _ = args


def handle_callback(
    callback: dict,
    token: str,
    api_url: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> None:
    """Handle inline keyboard callback (👍/👎 feedback)."""
    data = callback.get("data", "")
    callback_id = callback.get("id", "")
    from_user = callback.get("from", {})
    telegram_id = from_user.get("id")
    try:
        if data.startswith("fb:") and telegram_id is not None:
            parts = data.split(":")
            if len(parts) == 3 and parts[1] in ("g", "b"):
                feedback = "good" if parts[1] == "g" else "bad"
                job_id = int(parts[2])
                user_id = _resolve_user_id(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
                if user_id is not None:
                    post_feedback(api_url, user_id, telegram_id, job_id, feedback, api_auth_token, api_user_hmac_secret)
                    logger.info("feedback sent: job_id=%d feedback=%s", job_id, feedback)
                else:
                    logger.warning("feedback: could not resolve user_id for telegram_id=%s", telegram_id)
            elif data.startswith("fb:"):
                logger.warning("feedback: unknown callback_data format, ignoring: %s", data)
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
        _record_command("profile", "missing_start")
        send_message(token, chat_id, "Сначала отправьте /start")
        return True
    if put_user_profile(api_url, user_id, telegram_id, profile_text, api_auth_token, api_user_hmac_secret):
        _clear_conversation_state(telegram_id)
        _record_command("profile", "ok")
        send_message(token, chat_id, "Профиль обновлён.")
    else:
        _record_command("profile", "error")
        send_message(token, chat_id, "Ошибка обновления профиля.")
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
    if chat_id is None or telegram_id is None:
        return False

    if text == "/start":
        _clear_conversation_state(telegram_id)
        user_id = post_users(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
        if user_id is not None:
            _cache_user_id(telegram_id, user_id)
            _record_command("start", "ok")
            send_message(
                token,
                chat_id,
                "Добро пожаловать! Отправьте /profile и текст профиля для настройки.",
            )
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
        send_message(
            token,
            chat_id,
            (
                "<b>Команды бота:</b>\n"
                "/start — зарегистрироваться\n"
                "/profile &lt;текст&gt; — обновить профиль фрилансера\n"
                "/notify_hour &lt;0–23&gt; — выбрать час дайджеста по МСК (только Pro)\n"
                "/help — эта справка\n\n"
                "После обновления профиля ИИ подберёт подходящие заказы и пришлёт уведомления.\n"
                "Нажмите 👍 или 👎 под каждым заказом, чтобы обучить алгоритм."
            ),
        )
        return True

    if text == "/notify_hour" or text.startswith("/notify_hour "):
        rest = text[len("/notify_hour"):].strip()
        if not rest:
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

    pending_state = _get_conversation_state(telegram_id)
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
) -> bool:
    update_id = _extract_update_id(update)
    if update_id is not None and not _claim_update_id(update_id):
        logger.info("%s update skipped as duplicate: update_id=%s", transport, update_id)
        _METRICS.inc("telegram_bot_updates_total", transport=transport, result="duplicate")
        return True
    try:
        handled = _handle_update(update, token, api_url, api_auth_token, api_user_hmac_secret)
    except KeyboardInterrupt:
        if update_id is not None:
            _forget_update_id(update_id)
        raise
    except Exception as e:
        if update_id is not None:
            _forget_update_id(update_id)
        logger.error("%s update failed: update_id=%s err=%s", transport, update_id, _exception_name(e), exc_info=True)
        _METRICS.inc("telegram_bot_updates_total", transport=transport, result="failed")
        return False
    _METRICS.inc("telegram_bot_updates_total", transport=transport, result="handled" if handled else "ignored")
    return True


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
    if not set_webhook(token, webhook_url, webhook_secret, max_connections=max_connections):
        logger.error("webhook registration failed; aborting")
        sys.exit(1)

    _WebhookHandler.secret_token = webhook_secret
    _WebhookHandler.bot_token = token
    _WebhookHandler.api_url = api_url
    _WebhookHandler.api_auth_token = api_auth_token
    _WebhookHandler.api_user_hmac_secret = api_user_hmac_secret

    server = server_factory(("0.0.0.0", port), _WebhookHandler)
    logger.info("webhook server listening on port %d", port)

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

    try:
        server.serve_forever()
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        delete_webhook(token, drop_pending=False)
        logger.info("webhook server stopped")


def main() -> None:
    global _STATE_STORE
    _METRICS.set_ready(False)
    app_env = os.getenv("APP_ENV", "development")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    api_auth_token = os.getenv("API_AUTH_TOKEN")
    api_user_hmac_secret = os.getenv("API_USER_HMAC_SECRET")
    api_url = os.getenv("API_URL", "http://localhost:8080")
    try:
        _validate_secret("TELEGRAM_BOT_TOKEN", token or "", 20)
        _validate_secret("API_AUTH_TOKEN", api_auth_token or "", 32)
        _validate_secret("API_USER_HMAC_SECRET", api_user_hmac_secret or "", 32)
        if _is_production_env(app_env):
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
    metrics_bind = os.getenv("BOT_METRICS_BIND", DEFAULT_METRICS_BIND)
    metrics_port = _read_non_negative_int_env("BOT_METRICS_PORT", DEFAULT_METRICS_PORT)
    metrics_server = _start_metrics_server(metrics_bind, metrics_port)
    logger.info("bot started, API=%s mode=%s", api_url, bot_mode)
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
