from __future__ import annotations

import logging

from ai_service.usecase.rerank_policy import apply_rerank_policy
from ai_service.util import fallback_metrics


def test_apply_rerank_policy_logs_score_distribution_before_filter(caplog) -> None:
    fallback_metrics.reset_counters_for_tests()

    with caplog.at_level(logging.INFO):
        decision = apply_rerank_policy(
            [0.91, 0.42, 0.78],
            score_getter=float,
            threshold=0.55,
            top_k=2,
            fallback_enabled=True,
            pipeline="job",
            entity_name="job",
            entity_id=10,
        )

    assert decision.selected == [0.91, 0.78]
    assert decision.used_fallback is False
    assert "job_id=10: rerank scores before filter" in caplog.text
    assert "max=0.910" in caplog.text
    assert "min=0.420" in caplog.text
    assert "mean=0.703" in caplog.text


def test_apply_rerank_policy_uses_fallback_and_records_metrics_when_all_filtered(caplog) -> None:
    fallback_metrics.reset_counters_for_tests()

    with caplog.at_level(logging.WARNING):
        decision = apply_rerank_policy(
            [0.31, 0.22],
            score_getter=float,
            threshold=0.55,
            top_k=5,
            fallback_enabled=True,
            pipeline="job",
            entity_name="job",
            entity_id=77,
        )

    assert decision.selected == [0.31]
    assert decision.used_fallback is True
    assert decision.all_filtered is True
    assert "job_id=77: reranker filtered 100% of candidates" in caplog.text
    assert "falling back to top-1" in caplog.text

    rendered = fallback_metrics.render_prometheus_text()
    assert 'siteparser_rerank_all_filtered_total{pipeline="job"} 1.0' in rendered
    assert 'siteparser_rerank_fallback_total{pipeline="job"} 1.0' in rendered


def test_apply_rerank_policy_returns_empty_when_all_filtered_and_fallback_disabled(caplog) -> None:
    fallback_metrics.reset_counters_for_tests()

    with caplog.at_level(logging.WARNING):
        decision = apply_rerank_policy(
            [0.19, 0.18],
            score_getter=float,
            threshold=0.55,
            top_k=5,
            fallback_enabled=False,
            pipeline="ac_batch",
            entity_name="user",
            entity_id=5,
        )

    assert decision.selected == []
    assert decision.used_fallback is False
    assert decision.all_filtered is True
    assert "user_id=5: reranker filtered 100% of candidates" in caplog.text
    assert "fallback disabled" in caplog.text

    rendered = fallback_metrics.render_prometheus_text()
    assert 'siteparser_rerank_all_filtered_total{pipeline="ac_batch"} 1.0' in rendered
    assert "siteparser_rerank_fallback_total" not in rendered
