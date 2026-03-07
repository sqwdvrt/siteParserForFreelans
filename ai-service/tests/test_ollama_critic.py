"""Tests for OllamaCriticAgent."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ai_service.adapter.critic import OllamaCriticAgent
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User


def _user() -> User:
    return User(id=1, telegram_id=123, profile_text="Python backend", embedding=None)


def _selection() -> list[RankedJob]:
    return [
        RankedJob(
            job_id=1,
            title="Python role",
            why_it_fits="Django + PostgreSQL match.",
            rank=1,
            actor_confidence=0.9,
        )
    ]


def _mock_response(mock_open, payload: dict) -> None:
    mock_resp = mock_open.return_value.__enter__.return_value
    mock_resp.read.return_value = json.dumps(payload).encode()


@patch("ai_service.adapter.critic.ollama_critic._safe_open")
def test_valid_response_returns_critic_result(mock_open) -> None:
    _mock_response(
        mock_open,
        {"response": json.dumps({"score": 8.2, "critique": "Strong relevance and personalization."})},
    )
    critic = OllamaCriticAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = critic.evaluate(_user(), _selection())

    assert result.score == 8.2
    assert "relevance" in result.critique.lower()


@patch("ai_service.adapter.critic.ollama_critic._safe_open")
def test_score_is_clamped_to_ten(mock_open) -> None:
    _mock_response(
        mock_open,
        {"response": json.dumps({"score": 99, "critique": "Too high raw score"})},
    )
    critic = OllamaCriticAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = critic.evaluate(_user(), _selection())

    assert result.score == 10.0


@patch("ai_service.adapter.critic.ollama_critic._safe_open")
def test_invalid_json_returns_parse_error(mock_open) -> None:
    _mock_response(mock_open, {"response": "not json"})
    critic = OllamaCriticAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = critic.evaluate(_user(), _selection())

    assert result.score == 0.0
    assert result.critique == "Parse error."


def test_empty_selection_returns_zero_score() -> None:
    critic = OllamaCriticAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    result = critic.evaluate(_user(), [])

    assert result.score == 0.0
    assert result.critique == "Empty selection."


@patch("ai_service.adapter.critic.ollama_critic._safe_open")
def test_timeout_propagates(mock_open) -> None:
    mock_open.side_effect = TimeoutError("timed out")
    critic = OllamaCriticAgent(base_url="http://ollama:11434", model="llama3.2:3b")

    with pytest.raises(TimeoutError):
        critic.evaluate(_user(), _selection())


@patch("ai_service.adapter.critic.ollama_critic._safe_open")
def test_circuit_breaker_skips_repeated_timeouts(mock_open) -> None:
    mock_open.side_effect = TimeoutError("timed out")
    critic = OllamaCriticAgent(
        base_url="http://ollama:11434",
        model="llama3.2:3b",
        breaker_failure_threshold=2,
        breaker_open_interval_sec=60.0,
    )

    with pytest.raises(TimeoutError):
        critic.evaluate(_user(), _selection())
    with pytest.raises(TimeoutError):
        critic.evaluate(_user(), _selection())

    result = critic.evaluate(_user(), _selection())

    assert result.score == 0.0
    assert result.critique == "Circuit breaker open."
    assert mock_open.call_count == 2
