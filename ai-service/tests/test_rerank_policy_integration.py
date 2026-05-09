from __future__ import annotations

import logging
from unittest.mock import MagicMock

from ai_service.domain.job import Job
from ai_service.domain.user import User, UserPreferences
from ai_service.port.match_repository import MatchCandidate
from ai_service.usecase.process_ac_batch import ACBatch, ProcessACBatchUseCase
from ai_service.usecase.process_job import ProcessJobUseCase
from ai_service.util import fallback_metrics


def _user_with_profile() -> User:
    return User(
        id=1,
        telegram_id=123,
        profile_text="Python backend",
        embedding=[0.1] * 384,
        preferences=UserPreferences(preferred_sources=()),
    )


def test_process_job_falls_back_to_top_candidate_when_rerank_filters_all(caplog) -> None:
    fallback_metrics.reset_counters_for_tests()
    job = Job(id=1, title="Python API", description="FastAPI backend", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    candidates = [
        MatchCandidate(user_id=10, job_id=1, match_score=0.91, raw_similarity=0.91, profile_text="python fastapi"),
        MatchCandidate(user_id=20, job_id=1, match_score=0.88, raw_similarity=0.88, profile_text="golang"),
    ]
    match_repo.find_users_for_job.return_value = candidates
    reranker = MagicMock()
    reranker.model_name = "BAAI/bge-reranker-base"
    reranker.score_pairs.return_value = [0.31, 0.22]
    accumulate_matches = MagicMock()
    uc = ProcessJobUseCase(
        repo,
        emb,
        match_repo=match_repo,
        accumulate_matches=accumulate_matches,
        reranker=reranker,
        rerank_threshold=0.55,
        rerank_top_k=5,
        rerank_fallback_enabled=True,
    )

    with caplog.at_level(logging.INFO):
        assert uc.execute(1) is True

    sent = accumulate_matches.execute.call_args[0][0]
    assert [item.user_id for item in sent] == [10]
    assert "rerank scores before filter" in caplog.text
    assert "falling back to top-1" in caplog.text
    rendered = fallback_metrics.render_prometheus_text()
    assert 'siteparser_rerank_all_filtered_total{pipeline="job"} 1.0' in rendered


def test_process_ac_batch_falls_back_to_top_job_when_rerank_filters_all(caplog) -> None:
    fallback_metrics.reset_counters_for_tests()
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = _user_with_profile()
    job_repo = MagicMock()
    job_repo.get_with_scores.return_value = [
        (Job(id=1, title="A", description="Desc A", raw_html="<p>a</p>", rerank_score=0.31), 0.9),
        (Job(id=2, title="B", description="Desc B", raw_html="<p>b</p>", rerank_score=0.22), 0.8),
    ]
    pending_repo = MagicMock()
    actor = MagicMock()
    actor.explain_batch.return_value = ["Strong Python fit."]
    notify_queue = MagicMock()
    uc = ProcessACBatchUseCase(
        user_repo,
        job_repo,
        pending_repo,
        actor,
        notify_queue,
        rerank_threshold=0.55,
        rerank_fallback_enabled=True,
    )

    with caplog.at_level(logging.INFO):
        uc.execute(ACBatch(user_id=1, job_ids=[1, 2]))

    kwargs = notify_queue.enqueue_batch.call_args.kwargs
    assert [item.job_id for item in kwargs["ranked_jobs"]] == [1]
    assert "rerank scores before filter" in caplog.text
    assert "falling back to top-1" in caplog.text
    rendered = fallback_metrics.render_prometheus_text()
    assert 'siteparser_rerank_all_filtered_total{pipeline="ac_batch"} 1.0' in rendered
