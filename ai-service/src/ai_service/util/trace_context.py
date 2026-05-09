"""Trace context propagation helpers for async queue workers."""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_trace_id: ContextVar[str] = ContextVar("ai_trace_id", default="")


def normalize_trace_id(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if not value:
        return ""
    return value[:128]


def set_trace_id(trace_id: str) -> Token[str]:
    return _current_trace_id.set(normalize_trace_id(trace_id))


def reset_trace_id(token: Token[str]) -> None:
    _current_trace_id.reset(token)


def get_trace_id() -> str:
    return normalize_trace_id(_current_trace_id.get(""))
