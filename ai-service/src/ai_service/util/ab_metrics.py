"""A/B testing metrics tracking."""
import time
import threading
from dataclasses import dataclass, field
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class ABMetrics:
    """Thread-safe A/B test metrics per variant."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: dict[str, int] = defaultdict(int)
        self._successes: dict[str, int] = defaultdict(int)
        self._failures: dict[str, int] = defaultdict(int)
        self._feedback_good: dict[str, int] = defaultdict(int)
        self._feedback_bad: dict[str, int] = defaultdict(int)
        self._latency_sum: dict[str, float] = defaultdict(float)
        self._latency_count: dict[str, int] = defaultdict(int)
        self._recent_feedback: list[dict] = []

    def record_request(self, variant: str):
        with self._lock:
            self._requests[variant] += 1

    def record_success(self, variant: str):
        with self._lock:
            self._successes[variant] += 1

    def record_failure(self, variant: str):
        with self._lock:
            self._failures[variant] += 1

    def record_feedback(self, variant: str, is_good: bool):
        with self._lock:
            if is_good:
                self._feedback_good[variant] += 1
            else:
                self._feedback_bad[variant] += 1

            self._recent_feedback.append({
                "variant": variant,
                "good": is_good,
                "timestamp": time.time(),
            })
            if len(self._recent_feedback) > 1000:
                self._recent_feedback = self._recent_feedback[-1000:]

    def record_latency(self, variant: str, seconds: float):
        with self._lock:
            self._latency_sum[variant] += seconds
            self._latency_count[variant] += 1

    def get_variant_stats(self, variant: str) -> dict:
        with self._lock:
            total = self._requests.get(variant, 0)
            success = self._successes.get(variant, 0)
            failure = self._failures.get(variant, 0)
            good = self._feedback_good.get(variant, 0)
            bad = self._feedback_bad.get(variant, 0)
            feedback_total = good + bad
            latency_avg = (
                self._latency_sum.get(variant, 0.0)
                / max(self._latency_count.get(variant, 1), 1)
            )

            return {
                "requests": total,
                "successes": success,
                "failures": failure,
                "success_rate": success / max(total, 1),
                "feedback_good": good,
                "feedback_bad": bad,
                "feedback_positive_rate": good / max(feedback_total, 1),
                "avg_latency_seconds": round(latency_avg, 3),
            }

    def get_all_stats(self) -> dict[str, dict]:
        with self._lock:
            variants = set(self._requests.keys())
        return {v: self.get_variant_stats(v) for v in variants}

    def get_recent_feedback(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return self._recent_feedback[-limit:]

    def reset(self):
        with self._lock:
            self._requests.clear()
            self._successes.clear()
            self._failures.clear()
            self._feedback_good.clear()
            self._feedback_bad.clear()
            self._latency_sum.clear()
            self._latency_count.clear()
            self._recent_feedback.clear()


# Global instance
_metrics = ABMetrics()


def get_ab_metrics() -> ABMetrics:
    return _metrics
