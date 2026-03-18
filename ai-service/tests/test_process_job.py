"""Тесты ProcessJobUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_service.adapter.rule_based import RuleBasedClassifier
from ai_service.domain.job import Job
from ai_service.domain.user import User, UserPreferences
from ai_service.port.match_repository import MatchCandidate
from ai_service.port.repository import JobEmbeddingRecord
from ai_service.usecase.process_job import ProcessJobUseCase, _build_why_it_fits
from ai_service.util.trace_context import reset_trace_id, set_trace_id


def test_build_why_it_fits_empty_on_empty_classification() -> None:
    assert _build_why_it_fits({}) == ""


def test_build_why_it_fits_web_with_techs() -> None:
    result = _build_why_it_fits({"project_type": "web", "technologies": ["python", "react"]})
    assert "Веб-проект" in result
    assert "python" in result
    assert "react" in result


def test_build_why_it_fits_bot_with_seniority_and_budget() -> None:
    result = _build_why_it_fits({
        "project_type": "bot",
        "seniority": "senior",
        "budget_level": "high",
    })
    assert "Telegram-бот" in result
    assert "senior" in result
    assert "высокий бюджет" in result


def test_build_why_it_fits_unknown_seniority_and_budget_omitted() -> None:
    result = _build_why_it_fits({
        "project_type": "web",
        "seniority": "unknown",
        "budget_level": "unknown",
    })
    assert "уровень" not in result
    assert "бюджет" not in result


def test_build_why_it_fits_caps_technologies_at_three() -> None:
    result = _build_why_it_fits({"technologies": ["a", "b", "c", "d", "e"]})
    assert "d" not in result
    assert "e" not in result


def test_execute_returns_false_when_job_not_found() -> None:
    repo = MagicMock()
    repo.get.return_value = None
    emb = MagicMock()
    uc = ProcessJobUseCase(repo, emb)
    assert uc.execute(999) is False
    repo.get.assert_called_once_with(999)
    repo.save_embedding.assert_not_called()


def test_execute_skips_when_embedding_exists() -> None:
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = JobEmbeddingRecord(embedding=[0.2] * 384, metadata={})
    emb = MagicMock()
    match_repo = MagicMock()
    match_repo.find_users_for_job.return_value = []
    uc = ProcessJobUseCase(repo, emb, match_repo=match_repo)
    assert uc.execute(1) is True
    match_repo.find_users_for_job.assert_called_once_with(
        [0.2] * 384,
        1,
        0.7,
        20,
        allowed_user_ids=None,
        max_age_days=None,
    )
    emb.encode.assert_not_called()
    repo.save_embedding.assert_not_called()


def test_execute_saves_embedding_with_classification() -> None:
    job = Job(id=1, title="Python", description="Django", raw_html="<p>web</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test-model"
    classifier = RuleBasedClassifier()
    uc = ProcessJobUseCase(repo, emb, classifier)
    assert uc.execute(1) is True
    repo.save_embedding.assert_called_once()
    call_args = repo.save_embedding.call_args
    assert call_args[0][0] == 1
    assert len(call_args[0][1]) == 384
    meta = call_args[0][2]
    assert meta["model"] == "test-model"
    assert "text_length" in meta
    assert "classification" in meta
    assert meta["classification"].get("project_type") == "web"


def test_execute_calls_matching_after_save() -> None:
    """После save_embedding вызывается find_users_for_job при наличии match_repo."""
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    match_repo.find_users_for_job.return_value = []
    uc = ProcessJobUseCase(repo, emb, match_repo=match_repo)
    assert uc.execute(1) is True
    match_repo.find_users_for_job.assert_called_once()
    call = match_repo.find_users_for_job.call_args
    assert call[0][0] == [0.1] * 384
    assert call[0][1] == 1
    assert call[0][2] == 0.7
    assert call[0][3] == 20
    assert call.kwargs["max_age_days"] is None


def test_execute_accumulates_candidates_to_pending_repo() -> None:
    """При accumulate_matches кандидаты сохраняются в pending_ac_jobs."""
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    candidates = [
        MatchCandidate(user_id=10, job_id=1, match_score=0.85),
        MatchCandidate(user_id=20, job_id=1, match_score=0.72),
    ]
    match_repo.find_users_for_job.return_value = candidates
    accumulate_matches = MagicMock()
    uc = ProcessJobUseCase(
        repo,
        emb,
        match_repo=match_repo,
        accumulate_matches=accumulate_matches,
    )

    assert uc.execute(1) is True
    accumulate_matches.execute.assert_called_once()
    sent = accumulate_matches.execute.call_args[0][0]
    assert len(sent) == 2
    assert sent[0].user_id == 10
    assert sent[1].user_id == 20


def test_execute_propagates_accumulate_error() -> None:
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    candidates = [MatchCandidate(user_id=10, job_id=1, match_score=0.85)]
    match_repo.find_users_for_job.return_value = candidates
    accumulate_matches = MagicMock()
    accumulate_matches.execute.side_effect = RuntimeError("pending write failed")

    uc = ProcessJobUseCase(
        repo,
        emb,
        match_repo=match_repo,
        accumulate_matches=accumulate_matches,
    )

    with pytest.raises(RuntimeError, match="pending write failed"):
        uc.execute(1)


def test_execute_applies_rerank_threshold_and_top_k() -> None:
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
        MatchCandidate(user_id=30, job_id=1, match_score=0.82, raw_similarity=0.82, profile_text="backend python"),
    ]
    match_repo.find_users_for_job.return_value = candidates
    reranker = MagicMock()
    reranker.model_name = "BAAI/bge-reranker-base"
    reranker.score_pairs.return_value = [0.91, 0.42, 0.78]
    accumulate_matches = MagicMock()
    uc = ProcessJobUseCase(
        repo,
        emb,
        match_repo=match_repo,
        accumulate_matches=accumulate_matches,
        reranker=reranker,
        rerank_threshold=0.55,
        rerank_top_k=2,
    )

    assert uc.execute(1) is True
    reranker.score_pairs.assert_called_once()
    call = match_repo.find_users_for_job.call_args
    assert call[0][3] == 50
    assert call.kwargs["max_age_days"] is None
    sent = accumulate_matches.execute.call_args[0][0]
    assert [item.user_id for item in sent] == [10, 30]
    assert sent[0].rerank_score == pytest.approx(0.91)
    assert sent[0].final_score == pytest.approx(0.91)
    assert sent[1].rerank_score == pytest.approx(0.78)


def test_execute_applies_preference_filter_before_ann() -> None:
    job = Job(
        id=1,
        title="Python backend",
        description="FastAPI service",
        raw_html="<p>H</p>",
        source="kwork",
        budget="5000 руб",
    )
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    user_repo = MagicMock()
    user_repo.list_matchable_users.return_value = [
        User(id=10, telegram_id=1, profile_text="python", embedding=None),
        User(
            id=20,
            telegram_id=2,
            profile_text="python",
            embedding=None,
            preferences=UserPreferences(preferred_sources=("freelancehunt",)),
        ),
        User(
            id=30,
            telegram_id=3,
            profile_text="python",
            embedding=None,
            preferences=UserPreferences(exclude_keywords=("fastapi",)),
        ),
    ]
    match_repo = MagicMock()
    match_repo.find_users_for_job.return_value = [MatchCandidate(user_id=10, job_id=1, match_score=0.85)]
    accumulate_matches = MagicMock()
    uc = ProcessJobUseCase(
        repo,
        emb,
        match_repo=match_repo,
        accumulate_matches=accumulate_matches,
        user_repo=user_repo,
    )

    assert uc.execute(1) is True

    call = match_repo.find_users_for_job.call_args
    assert call.kwargs["allowed_user_ids"] == [10]
    assert call.kwargs["max_age_days"] is None


def test_execute_passes_explicit_match_age_gate_to_repo() -> None:
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    match_repo.find_users_for_job.return_value = []
    uc = ProcessJobUseCase(repo, emb, match_repo=match_repo, match_max_age_days=2)

    assert uc.execute(1) is True

    call = match_repo.find_users_for_job.call_args
    assert call.kwargs["max_age_days"] == 2


def test_execute_records_budget_filter_events() -> None:
    job = Job(
        id=1,
        title="Python backend",
        description="FastAPI service",
        raw_html="<p>H</p>",
        source="kwork",
        budget="800 руб",
    )
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    user_repo = MagicMock()
    user_repo.list_matchable_users.return_value = [
        User(
            id=10,
            telegram_id=1,
            profile_text="python",
            embedding=None,
            preferences=UserPreferences(min_budget=1000.0),
        ),
        User(
            id=20,
            telegram_id=2,
            profile_text="python",
            embedding=None,
        ),
    ]
    match_repo = MagicMock()
    match_repo.find_users_for_job.return_value = [MatchCandidate(user_id=20, job_id=1, match_score=0.85)]
    filter_event_repo = MagicMock()
    uc = ProcessJobUseCase(
        repo,
        emb,
        match_repo=match_repo,
        user_repo=user_repo,
        filter_event_repo=filter_event_repo,
    )

    assert uc.execute(1) is True

    filter_event_repo.record_events.assert_called_once_with(
        job_id=1,
        user_ids=[10],
        reason="budget",
        trace_id="",
    )


def test_execute_skips_ann_when_preference_filter_excludes_all_users() -> None:
    job = Job(id=1, title="PHP backend", description="Laravel support", raw_html="<p>H</p>", source="kwork")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    user_repo = MagicMock()
    user_repo.list_matchable_users.return_value = [
        User(
            id=20,
            telegram_id=2,
            profile_text="python",
            embedding=None,
            preferences=UserPreferences(exclude_keywords=("laravel",)),
        ),
    ]
    match_repo = MagicMock()
    uc = ProcessJobUseCase(
        repo,
        emb,
        match_repo=match_repo,
        user_repo=user_repo,
    )

    assert uc.execute(1) is True

    match_repo.find_users_for_job.assert_not_called()


def test_execute_sets_why_it_fits_from_classification() -> None:
    """why_it_fits из классификатора прокидывается в кандидатов."""
    job = Job(id=1, title="Python web", description="Django backend", raw_html="<p>web</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    candidates = [MatchCandidate(user_id=10, job_id=1, match_score=0.85)]
    match_repo.find_users_for_job.return_value = candidates
    accumulate_matches = MagicMock()
    classifier = RuleBasedClassifier()
    uc = ProcessJobUseCase(
        repo, emb, classifier=classifier, match_repo=match_repo,
        accumulate_matches=accumulate_matches,
    )
    uc.execute(1)
    sent = accumulate_matches.execute.call_args[0][0]
    assert len(sent) == 1
    assert sent[0].why_it_fits != "", "why_it_fits должен быть заполнен при наличии классификатора"
    assert "python" in sent[0].why_it_fits.lower() or "веб" in sent[0].why_it_fits.lower()


def test_execute_why_it_fits_empty_without_classifier() -> None:
    """Без классификатора why_it_fits остаётся пустым."""
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    candidates = [MatchCandidate(user_id=10, job_id=1, match_score=0.85)]
    match_repo.find_users_for_job.return_value = candidates
    accumulate_matches = MagicMock()
    uc = ProcessJobUseCase(repo, emb, match_repo=match_repo, accumulate_matches=accumulate_matches)
    uc.execute(1)
    sent = accumulate_matches.execute.call_args[0][0]
    assert sent[0].why_it_fits == ""


def test_execute_propagates_trace_id_to_candidates() -> None:
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = None
    emb = MagicMock()
    emb.encode.return_value = [0.1] * 384
    emb.model_name = "test"
    match_repo = MagicMock()
    candidates = [MatchCandidate(user_id=10, job_id=1, match_score=0.85)]
    match_repo.find_users_for_job.return_value = candidates
    accumulate_matches = MagicMock()
    uc = ProcessJobUseCase(repo, emb, match_repo=match_repo, accumulate_matches=accumulate_matches)

    token = set_trace_id("trace-ai-1")
    try:
        assert uc.execute(1) is True
    finally:
        reset_trace_id(token)

    sent = accumulate_matches.execute.call_args[0][0]
    assert sent[0].trace_id == "trace-ai-1"


def test_execute_reuses_saved_classification_when_reprocessing() -> None:
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = JobEmbeddingRecord(
        embedding=[0.2] * 384,
        metadata={
            "classification": {
                "project_type": "web",
                "technologies": ["python", "fastapi"],
            }
        },
    )
    emb = MagicMock()
    match_repo = MagicMock()
    match_repo.find_users_for_job.return_value = [MatchCandidate(user_id=10, job_id=1, match_score=0.9)]
    accumulate_matches = MagicMock()
    uc = ProcessJobUseCase(repo, emb, match_repo=match_repo, accumulate_matches=accumulate_matches)

    assert uc.execute(1) is True

    sent = accumulate_matches.execute.call_args[0][0]
    assert "python" in sent[0].why_it_fits.lower()
    emb.encode.assert_not_called()


def test_execute_accepts_numpy_saved_embedding_for_retry() -> None:
    np = pytest.importorskip("numpy")
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.get_embedding.return_value = JobEmbeddingRecord(
        embedding=np.array([0.2] * 384),
        metadata={},
    )
    emb = MagicMock()
    match_repo = MagicMock()
    match_repo.find_users_for_job.return_value = []
    uc = ProcessJobUseCase(repo, emb, match_repo=match_repo)

    assert uc.execute(1) is True

    match_repo.find_users_for_job.assert_called_once()
    emb.encode.assert_not_called()
