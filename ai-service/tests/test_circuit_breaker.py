"""Tests for CircuitBreaker utility."""

from __future__ import annotations

from ai_service.util.circuit_breaker import CircuitBreaker


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_circuit_breaker_opens_after_failure_threshold() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=2, open_interval_sec=30.0, clock=clock)

    assert breaker.allow_request() is True
    breaker.record_failure()
    assert breaker.allow_request() is True
    breaker.record_failure()

    assert breaker.allow_request() is False


def test_circuit_breaker_allows_single_probe_after_timeout_and_recovers() -> None:
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=1, open_interval_sec=10.0, clock=clock)

    assert breaker.allow_request() is True
    breaker.record_failure()
    assert breaker.allow_request() is False

    clock.advance(10.0)
    assert breaker.allow_request() is True
    assert breaker.allow_request() is False

    breaker.record_success()
    assert breaker.allow_request() is True
