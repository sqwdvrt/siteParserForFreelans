"""Tests for GeminiClassifier."""

from __future__ import annotations

import json
from unittest.mock import patch

from ai_service.adapter.gemini import GeminiClassifier


def _gemini_response(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _mock_urlopen(mock_open, payload: dict) -> None:
    mock_resp = mock_open.return_value.__enter__.return_value
    mock_resp.read.return_value = json.dumps(payload).encode()


@patch("urllib.request.urlopen")
def test_valid_response_parses_classification(mock_open) -> None:
    _mock_urlopen(
        mock_open,
        _gemini_response(json.dumps({
            "project_type": "web",
            "seniority": "middle",
            "technologies": ["Python", "Django"],
            "complexity": "medium",
            "budget_level": "high",
            "is_spam": False,
        })),
    )
    classifier = GeminiClassifier(api_key="test-key", model="gemini-1.5-flash")

    result = classifier.classify("Build a Django web app")

    assert result["project_type"] == "web"
    assert result["seniority"] == "middle"
    assert "Python" in result["technologies"]
    assert result["is_spam"] is False


@patch("urllib.request.urlopen")
def test_is_spam_true_detected(mock_open) -> None:
    _mock_urlopen(
        mock_open,
        _gemini_response(json.dumps({"is_spam": True})),
    )
    classifier = GeminiClassifier(api_key="test-key")

    result = classifier.classify("Click here to earn $$$")

    assert result["is_spam"] is True


@patch("urllib.request.urlopen")
def test_invalid_json_returns_empty(mock_open) -> None:
    _mock_urlopen(mock_open, _gemini_response("not json"))
    classifier = GeminiClassifier(api_key="test-key")

    result = classifier.classify("some project text")

    assert result == {}


@patch("urllib.request.urlopen")
def test_empty_candidates_returns_empty(mock_open) -> None:
    _mock_urlopen(mock_open, {"candidates": []})
    classifier = GeminiClassifier(api_key="test-key")

    result = classifier.classify("some project text")

    assert result == {}


def test_empty_text_returns_not_spam_without_api_call() -> None:
    classifier = GeminiClassifier(api_key="test-key")

    result = classifier.classify("")

    assert result == {"is_spam": False}


@patch("urllib.request.urlopen")
def test_circuit_breaker_opens_after_failures(mock_open) -> None:
    import urllib.error
    mock_open.side_effect = urllib.error.URLError("connection refused")
    classifier = GeminiClassifier(
        api_key="test-key",
        breaker_failure_threshold=2,
        breaker_open_interval_sec=60.0,
    )

    classifier.classify("text 1")
    classifier.classify("text 2")
    result = classifier.classify("text 3")

    assert result == {}
    assert mock_open.call_count == 2
