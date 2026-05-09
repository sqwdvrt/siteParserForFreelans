"""In-process counters for LLM primary/fallback behavior with Prometheus text endpoint.

Also exposes PostgreSQL connection pool gauges (active/idle/max) registered by
PooledPostgresRepository instances via register_pool_provider / deregister_pool_provider.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TypedDict

logger = logging.getLogger(__name__)

_DEFAULT_BIND = "0.0.0.0"
_PRIMARY_METRIC_NAME = "ai_llm_primary_total"
_FALLBACK_METRIC_NAME = "ai_llm_fallback_total"
_PG_POOL_ACTIVE_METRIC = "ai_pg_pool_connections_active"
_PG_POOL_IDLE_METRIC = "ai_pg_pool_connections_idle"
_PG_POOL_MAX_METRIC = "ai_pg_pool_connections_max"

_primary_counters: dict[tuple[str, str], int] = defaultdict(int)
_fallback_counters: dict[tuple[str, str], int] = defaultdict(int)
_custom_metrics: dict[str, dict[str, object]] = {}
_counters_lock = threading.Lock()

# Pool metrics registry: pool_name → callable returning PoolStats
class PoolStats(TypedDict):
    active: int
    idle: int
    max: int

_pool_providers: dict[str, Callable[[], PoolStats]] = {}
# RLock is required: registering a new provider under an existing key drops the
# old bound-method's refcount inside the lock, which (in CPython) synchronously
# calls __del__ on the old repo → close() → deregister_pool_provider() →
# re-acquires the same lock.  A plain Lock would deadlock; RLock allows re-entry
# from the same thread.
_pool_providers_lock = threading.RLock()


def register_pool_provider(name: str, provider: Callable[[], PoolStats]) -> None:
    """Register a connection pool stats provider for Prometheus export."""
    with _pool_providers_lock:
        _pool_providers[name] = provider


def deregister_pool_provider(name: str) -> None:
    """Deregister a pool stats provider (call on pool close)."""
    with _pool_providers_lock:
        _pool_providers.pop(name, None)


def snapshot_pool_stats() -> dict[str, PoolStats]:
    """Return a snapshot of all registered pool stats."""
    with _pool_providers_lock:
        providers = dict(_pool_providers)
    result: dict[str, PoolStats] = {}
    for name, fn in providers.items():
        try:
            result[name] = fn()
        except Exception:  # noqa: BLE001
            pass
    return result

_servers_by_addr: dict[tuple[str, int], ThreadingHTTPServer] = {}
_servers_lock = threading.Lock()


def record_primary_outcome(pipeline: str, outcome: str) -> None:
    normalized_pipeline = _normalize_label_value(pipeline)
    normalized_outcome = _normalize_label_value(outcome)
    with _counters_lock:
        _primary_counters[(normalized_pipeline, normalized_outcome)] += 1


def record_fallback(pipeline: str, reason: str) -> None:
    normalized_pipeline = _normalize_label_value(pipeline)
    normalized_reason = _normalize_label_value(reason)
    with _counters_lock:
        _fallback_counters[(normalized_pipeline, normalized_reason)] += 1


def snapshot_counters() -> dict[str, dict[tuple[str, str], int]]:
    with _counters_lock:
        return {
            "primary": dict(_primary_counters),
            "fallback": dict(_fallback_counters),
        }


def reset_counters_for_tests() -> None:
    with _counters_lock:
        _primary_counters.clear()
        _fallback_counters.clear()
        _custom_metrics.clear()


def set_gauge(
    name: str,
    help_text: str,
    value: float,
    *,
    labels: dict[str, str] | None = None,
) -> None:
    _set_custom_metric(name, "gauge", help_text, float(value), labels=labels)


def increment_counter(
    name: str,
    help_text: str,
    amount: float = 1.0,
    *,
    labels: dict[str, str] | None = None,
) -> None:
    _set_custom_metric(name, "counter", help_text, float(amount), labels=labels, accumulate=True)


def render_prometheus_text() -> str:
    snapshot = snapshot_counters()
    lines = [
        f"# HELP {_PRIMARY_METRIC_NAME} Count of primary LLM attempts by pipeline and outcome.",
        f"# TYPE {_PRIMARY_METRIC_NAME} counter",
    ]
    for (pipeline, outcome), value in sorted(snapshot["primary"].items()):
        pipeline_label = _escape_label_value(pipeline)
        outcome_label = _escape_label_value(outcome)
        lines.append(
            f'{_PRIMARY_METRIC_NAME}{{pipeline="{pipeline_label}",'
            f'outcome="{outcome_label}"}} {value}'
        )

    lines.extend(
        [
            f"# HELP {_FALLBACK_METRIC_NAME} Count of fallback activations when primary LLM path is unusable.",
            f"# TYPE {_FALLBACK_METRIC_NAME} counter",
        ]
    )
    for (pipeline, reason), value in sorted(snapshot["fallback"].items()):
        pipeline_label = _escape_label_value(pipeline)
        reason_label = _escape_label_value(reason)
        lines.append(
            f'{_FALLBACK_METRIC_NAME}{{pipeline="{pipeline_label}",'
            f'reason="{reason_label}"}} {value}'
        )

    custom_metrics_snapshot = _snapshot_custom_metrics()
    for metric_name, spec in sorted(custom_metrics_snapshot.items()):
        lines.extend([
            f"# HELP {metric_name} {spec['help']}",
            f"# TYPE {metric_name} {spec['type']}",
        ])
        samples = spec["samples"]
        assert isinstance(samples, dict)
        for labels, value in sorted(samples.items()):
            label_text = _format_labels(labels)
            if label_text:
                lines.append(f"{metric_name}{{{label_text}}} {value}")
            else:
                lines.append(f"{metric_name} {value}")

    # PostgreSQL connection pool gauges
    pool_snapshot = snapshot_pool_stats()
    if pool_snapshot:
        lines.extend([
            f"# HELP {_PG_POOL_ACTIVE_METRIC} PostgreSQL connections currently checked out from pool.",
            f"# TYPE {_PG_POOL_ACTIVE_METRIC} gauge",
        ])
        for pool_name, stats in sorted(pool_snapshot.items()):
            label = f'pool="{_escape_label_value(pool_name)}"'
            lines.append(f"{_PG_POOL_ACTIVE_METRIC}{{{label}}} {stats['active']}")

        lines.extend([
            f"# HELP {_PG_POOL_IDLE_METRIC} PostgreSQL connections idle and available in pool.",
            f"# TYPE {_PG_POOL_IDLE_METRIC} gauge",
        ])
        for pool_name, stats in sorted(pool_snapshot.items()):
            label = f'pool="{_escape_label_value(pool_name)}"'
            lines.append(f"{_PG_POOL_IDLE_METRIC}{{{label}}} {stats['idle']}")

        lines.extend([
            f"# HELP {_PG_POOL_MAX_METRIC} Maximum connections allowed in pool.",
            f"# TYPE {_PG_POOL_MAX_METRIC} gauge",
        ])
        for pool_name, stats in sorted(pool_snapshot.items()):
            label = f'pool="{_escape_label_value(pool_name)}"'
            lines.append(f"{_PG_POOL_MAX_METRIC}{{{label}}} {stats['max']}")

    return "\n".join(lines) + "\n"


def start_metrics_server(
    *,
    bind: str = _DEFAULT_BIND,
    port: int = 0,
) -> bool:
    if port <= 0:
        return False
    normalized_bind = (bind or "").strip() or _DEFAULT_BIND
    addr = (normalized_bind, port)
    with _servers_lock:
        if addr in _servers_by_addr:
            return True
        try:
            server = ThreadingHTTPServer(addr, _MetricsHandler)
        except OSError as exc:
            logger.error("failed to start fallback metrics server on %s:%d: %s", normalized_bind, port, exc)
            return False
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.5},
            daemon=True,
            name=f"fallback-metrics-{normalized_bind}:{port}",
        )
        thread.start()
        _servers_by_addr[addr] = server
    logger.info("fallback metrics endpoint started at http://%s:%d/metrics", normalized_bind, port)
    return True


def start_metrics_server_from_env(
    *,
    port_env: str,
    bind_env: str = "AI_METRICS_BIND",
    default_port: int = 0,
    default_bind: str = _DEFAULT_BIND,
) -> bool:
    bind = os.getenv(bind_env, default_bind)
    raw_port = os.getenv(port_env, str(default_port))
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        logger.warning("invalid %s=%r, fallback to %d (metrics disabled when <=0)", port_env, raw_port, default_port)
        port = default_port
    if port <= 0:
        return False
    return start_metrics_server(bind=bind, port=port)


def _normalize_label_value(raw: str) -> str:
    value = str(raw).strip()
    if not value:
        return "unknown"
    return value[:64]


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _normalize_labels(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    normalized: list[tuple[str, str]] = []
    for key, value in sorted(labels.items()):
        normalized.append((_normalize_label_value(key), _normalize_label_value(value)))
    return tuple(normalized)


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in labels)


def _set_custom_metric(
    name: str,
    metric_type: str,
    help_text: str,
    value: float,
    *,
    labels: dict[str, str] | None = None,
    accumulate: bool = False,
) -> None:
    normalized_name = str(name).strip()
    if not normalized_name:
        return
    label_key = _normalize_labels(labels)
    with _counters_lock:
        spec = _custom_metrics.setdefault(
            normalized_name,
            {
                "type": metric_type,
                "help": str(help_text).strip() or normalized_name,
                "samples": {},
            },
        )
        samples = spec["samples"]
        assert isinstance(samples, dict)
        if accumulate:
            samples[label_key] = float(samples.get(label_key, 0.0)) + value
        else:
            samples[label_key] = value


def _snapshot_custom_metrics() -> dict[str, dict[str, object]]:
    with _counters_lock:
        return {
            name: {
                "type": spec["type"],
                "help": spec["help"],
                "samples": dict(spec["samples"]),
            }
            for name, spec in _custom_metrics.items()
        }


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/metrics", "/metrics/"}:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        payload = render_prometheus_text().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        _ = fmt
        _ = args
        return
