"""Tests for fallback metrics and instrumentation in fallback adapters."""

from __future__ import annotations

from unittest.mock import MagicMock

from ai_service.adapter.actor import FallbackActorAgent
from ai_service.adapter.critic import FallbackCriticAgent
from ai_service.adapter.fallback import FallbackClassifier
from ai_service.domain.critic_result import CriticResult
from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.util import fallback_metrics


def _user() -> User:
    return User(id=1, telegram_id=123, profile_text="Python backend", embedding=None)


def _candidates() -> list[Job]:
    return [Job(id=1, title="A", description="Desc A", raw_html="<p>a</p>")]


def _selection() -> list[RankedJob]:
    return [
        RankedJob(
            job_id=1,
            title="A",
            why_it_fits="Matches profile.",
            rank=1,
            actor_confidence=0.9,
        )
    ]


def test_render_prometheus_text_contains_primary_and_fallback_counters() -> None:
    fallback_metrics.reset_counters_for_tests()
    fallback_metrics.record_primary_outcome("classifier", "success")
    fallback_metrics.record_fallback("classifier", "primary_error")

    rendered = fallback_metrics.render_prometheus_text()

    assert 'ai_llm_primary_total{pipeline="classifier",outcome="success"} 1' in rendered
    assert 'ai_llm_fallback_total{pipeline="classifier",reason="primary_error"} 1' in rendered


def test_render_prometheus_text_contains_custom_metrics() -> None:
    fallback_metrics.reset_counters_for_tests()
    fallback_metrics.set_gauge("ai_pending_ac_jobs_rows", "Pending AC rows.", 12, labels={"state": "total"})
    fallback_metrics.increment_counter("ai_pending_ac_jobs_cleanup_total", "Cleanup total.", 3)

    rendered = fallback_metrics.render_prometheus_text()

    assert "# HELP ai_pending_ac_jobs_rows Pending AC rows." in rendered
    assert "# TYPE ai_pending_ac_jobs_rows gauge" in rendered
    assert 'ai_pending_ac_jobs_rows{state="total"} 12.0' in rendered
    assert "# HELP ai_pending_ac_jobs_cleanup_total Cleanup total." in rendered
    assert "# TYPE ai_pending_ac_jobs_cleanup_total counter" in rendered
    assert "ai_pending_ac_jobs_cleanup_total 3.0" in rendered


def test_classifier_primary_error_records_fallback_metric() -> None:
    fallback_metrics.reset_counters_for_tests()
    primary = MagicMock()
    primary.classify.side_effect = RuntimeError("primary down")
    fallback = MagicMock()
    fallback.classify.return_value = {"project_type": "web"}
    classifier = FallbackClassifier(primary=primary, fallback=fallback)

    result = classifier.classify("test project")

    assert result["project_type"] == "web"
    snapshot = fallback_metrics.snapshot_counters()
    assert snapshot["primary"][("classifier", "error")] == 1
    assert snapshot["fallback"][("classifier", "primary_error")] == 1


def test_actor_primary_empty_records_fallback_metric() -> None:
    fallback_metrics.reset_counters_for_tests()
    primary = MagicMock()
    primary.select.return_value = []
    fallback = MagicMock()
    fallback.select.return_value = _selection()
    agent = FallbackActorAgent(primary=primary, fallback=fallback)

    result = agent.select(user=_user(), candidates=_candidates(), max_jobs=1, critique=None)

    assert len(result) == 1
    snapshot = fallback_metrics.snapshot_counters()
    assert snapshot["primary"][("actor", "empty")] == 1
    assert snapshot["fallback"][("actor", "primary_empty")] == 1


def test_critic_primary_error_records_fallback_metric() -> None:
    fallback_metrics.reset_counters_for_tests()
    primary = MagicMock()
    primary.evaluate.side_effect = RuntimeError("primary critic unavailable")
    fallback = MagicMock()
    fallback.evaluate.return_value = CriticResult(score=5.0, critique="fallback")
    agent = FallbackCriticAgent(primary=primary, fallback=fallback)

    result = agent.evaluate(user=_user(), selection=_selection())

    assert result.score == 5.0
    snapshot = fallback_metrics.snapshot_counters()
    assert snapshot["primary"][("critic", "error")] == 1
    assert snapshot["fallback"][("critic", "primary_error")] == 1


def test_critic_primary_invalid_records_fallback_metric() -> None:
    fallback_metrics.reset_counters_for_tests()
    primary = MagicMock()
    primary.evaluate.return_value = CriticResult(score=0.0, critique="Circuit breaker open.")
    fallback = MagicMock()
    fallback.evaluate.return_value = CriticResult(score=5.0, critique="fallback")
    agent = FallbackCriticAgent(primary=primary, fallback=fallback)

    result = agent.evaluate(user=_user(), selection=_selection())

    assert result.score == 5.0
    snapshot = fallback_metrics.snapshot_counters()
    assert snapshot["primary"][("critic", "invalid")] == 1
    assert snapshot["fallback"][("critic", "primary_invalid")] == 1
