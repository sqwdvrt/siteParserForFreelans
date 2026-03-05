"""OpenTelemetry tracing setup for ai-service."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


def init_tracer(service_name: str) -> object | None:
    """Инициализирует глобальный TracerProvider.

    Если OTEL_EXPORTER_OTLP_ENDPOINT не задан или пакеты не установлены — no-op.
    Возвращает provider или None.
    """
    if not _OTEL_AVAILABLE:
        logger.debug("opentelemetry-sdk not installed, tracing disabled")
        return None

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.debug("OTEL_EXPORTER_OTLP_ENDPOINT not set, tracing disabled")
        return None

    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider = TracerProvider(resource=Resource({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        logger.info("tracing enabled, service=%s endpoint=%s", service_name, endpoint)
        return provider
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracer init failed, tracing disabled: %s", exc)
        return None


def extract_context(traceparent: str) -> object:
    """Восстанавливает OTel Context из traceparent строки. Возвращает Context (может быть пустым)."""
    if not _OTEL_AVAILABLE or not traceparent:
        return _empty_context()
    try:
        carrier: dict[str, str] = {"traceparent": traceparent}
        return TraceContextTextMapPropagator().extract(carrier)
    except Exception as exc:  # noqa: BLE001
        logger.debug("extract_context failed: %s", exc)
        return _empty_context()


def inject_context(ctx: object) -> str:
    """Сериализует текущий OTel trace context в traceparent строку."""
    if not _OTEL_AVAILABLE:
        return ""
    try:
        carrier: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(carrier, ctx)
        return carrier.get("traceparent", "")
    except Exception as exc:  # noqa: BLE001
        logger.debug("inject_context failed: %s", exc)
        return ""


def _empty_context() -> object:
    if _OTEL_AVAILABLE:
        from opentelemetry import context as _ctx
        return _ctx.get_current()
    return object()
