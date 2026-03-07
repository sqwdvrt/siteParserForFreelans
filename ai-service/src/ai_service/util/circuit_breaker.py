"""Small thread-safe circuit breaker for flaky upstreams."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class CircuitBreaker:
    """Open after repeated failures and allow a single recovery probe later."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        open_interval_sec: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._failure_threshold = max(1, int(failure_threshold))
        self._open_interval_sec = max(0.1, float(open_interval_sec))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_until = 0.0
        self._probe_in_flight = False

    def allow_request(self) -> bool:
        now = self._clock()
        with self._lock:
            if self._opened_until <= 0:
                return True
            if now < self._opened_until:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_until = 0.0
            self._probe_in_flight = False

    def record_failure(self) -> None:
        now = self._clock()
        with self._lock:
            if self._opened_until > 0 or self._probe_in_flight:
                self._consecutive_failures = self._failure_threshold
            else:
                self._consecutive_failures += 1
            self._probe_in_flight = False
            if self._consecutive_failures >= self._failure_threshold:
                self._opened_until = now + self._open_interval_sec

