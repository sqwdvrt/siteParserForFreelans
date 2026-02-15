"""Тесты ProcessJobUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_service.adapter.rule_based import RuleBasedClassifier
from ai_service.domain.job import Job
from ai_service.usecase.process_job import ProcessJobUseCase


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
