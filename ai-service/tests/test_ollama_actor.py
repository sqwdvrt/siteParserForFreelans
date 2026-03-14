"""Tests for OllamaActorAgent."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ai_service.adapter.actor import OllamaActorAgent
from ai_service.domain.job import Job
from ai_service.domain.user import User


def _user() -> User:
    return User(id=1, telegram_id=123, profile_text="Python backend", embedding=None)


def _jobs() -> list[Job]:
    return [
        Job(id=1, title="Python role", description="Need Python", raw_html="<p>1</p>"),
        Job(id=2, title="Django role", description="Need Django", raw_html="<p>2</p>"),
        Job(id=3, title="React role", description="Need React", raw_html="<p>3</p>"),
    ]


def _mock_response(mock_open, payload: dict) -> None:
    mock_resp = mock_open.return_value.__enter__.return_value
    mock_resp.read.return_value = json.dumps(payload).encode()


@patch("ai_service.adapter.actor.ollama_actor._safe_open")
def test_valid_response_returns_ranked_jobs(mock_open) -> None:
    _mock_response(
        mock_open,
        {
            "response": json.dumps(
                {
                    "selected": [
                        {"job_id": 1, "rank": 1, "why_it_fits": "Python match", "confidence": 0.9},
                        {"job_id": 2, "rank": 2, "why_it_fits": "Django exp", "confidence": 0.7},
                    ]
                }
            )
        },
    )
    actor = OllamaActorAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = actor.select(_user(), _jobs())

    assert len(result) == 2
    assert result[0].job_id == 1
    assert result[0].rank == 1
    assert result[1].job_id == 2
    assert result[1].rank == 2


@patch("ai_service.adapter.actor.ollama_actor._safe_open")
def test_invalid_json_returns_empty(mock_open) -> None:
    _mock_response(mock_open, {"response": "not json"})
    actor = OllamaActorAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = actor.select(_user(), _jobs()[:1])

    assert result == []


@patch("ai_service.adapter.actor.ollama_actor._safe_open")
def test_unknown_job_id_in_response_is_skipped(mock_open) -> None:
    _mock_response(
        mock_open,
        {"response": json.dumps({"selected": [{"job_id": 999, "rank": 1, "why_it_fits": "x"}]})},
    )
    actor = OllamaActorAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = actor.select(_user(), _jobs()[:1])

    assert result == []


@patch("ai_service.adapter.actor.ollama_actor._safe_open")
def test_timeout_propagates(mock_open) -> None:
    mock_open.side_effect = TimeoutError("timed out")
    actor = OllamaActorAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    with pytest.raises(TimeoutError):
        actor.select(_user(), _jobs()[:1])


@patch("ai_service.adapter.actor.ollama_actor._safe_open")
def test_circuit_breaker_skips_repeated_timeouts(mock_open) -> None:
    mock_open.side_effect = TimeoutError("timed out")
    actor = OllamaActorAgent(
        base_url="http://ollama:11434",
        model="llama3.2:3b",
        breaker_failure_threshold=2,
        breaker_open_interval_sec=60.0,
    )

    with pytest.raises(TimeoutError):
        actor.select(_user(), _jobs()[:1])
    with pytest.raises(TimeoutError):
        actor.select(_user(), _jobs()[:1])

    result = actor.select(_user(), _jobs()[:1])

    assert result == []
    assert mock_open.call_count == 2


@patch("ai_service.adapter.actor.ollama_actor._safe_open")
def test_explain_batch_returns_explanations_in_order(mock_open) -> None:
    _mock_response(
        mock_open,
        {
            "response": json.dumps(
                {
                    "explanations": [
                        "Python match",
                        "Django match",
                    ]
                }
            )
        },
    )
    actor = OllamaActorAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = actor.explain_batch(_user(), _jobs()[:2])

    assert result == ["Python match", "Django match"]


@patch("ai_service.adapter.actor.ollama_actor._safe_open")
def test_explain_batch_invalid_length_returns_empty(mock_open) -> None:
    _mock_response(mock_open, {"response": json.dumps({"explanations": ["only one"]})})
    actor = OllamaActorAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = actor.explain_batch(_user(), _jobs()[:2])

    assert result == []
