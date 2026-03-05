"""Тесты ProcessJobUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_service.adapter.rule_based import RuleBasedClassifier
from ai_service.domain.job import Job
from ai_service.port.match_repository import MatchCandidate
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
    repo.has_embedding.return_value = True
    emb = MagicMock()
    uc = ProcessJobUseCase(repo, emb)
    assert uc.execute(1) is False
    repo.has_embedding.assert_called_once_with(1)
    repo.save_embedding.assert_not_called()


def test_execute_saves_embedding_with_classification() -> None:
    job = Job(id=1, title="Python", description="Django", raw_html="<p>web</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.has_embedding.return_value = False
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
    repo.has_embedding.return_value = False
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




def test_execute_accumulates_candidates_to_pending_repo() -> None:
    """При accumulate_matches кандидаты сохраняются в pending_ac_jobs."""
    job = Job(id=1, title="T", description="D", raw_html="<p>H</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.has_embedding.return_value = False
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
    repo.has_embedding.return_value = False
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


def test_execute_sets_why_it_fits_from_classification() -> None:
    """why_it_fits из классификатора прокидывается в кандидатов."""
    job = Job(id=1, title="Python web", description="Django backend", raw_html="<p>web</p>")
    repo = MagicMock()
    repo.get.return_value = job
    repo.has_embedding.return_value = False
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
    repo.has_embedding.return_value = False
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
    repo.has_embedding.return_value = False
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
