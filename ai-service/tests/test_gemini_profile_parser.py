"""Tests for Gemini profile parser."""

from __future__ import annotations

import json
from unittest.mock import patch

from ai_service.adapter.gemini.profile_parser import GeminiProfileParser


def _gemini_response(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _mock_urlopen(mock_open, payload: dict) -> None:
    mock_resp = mock_open.return_value.__enter__.return_value
    mock_resp.read.return_value = json.dumps(payload).encode()


@patch("urllib.request.urlopen")
def test_profile_parser_returns_normalized_payload(mock_open) -> None:
    _mock_urlopen(
        mock_open,
        _gemini_response(json.dumps({
            "stack": ["FastAPI", " PostgreSQL ", "fastapi"],
            "specialization": "backend",
            "level": "senior",
            "preferred_work_type": "remote",
            "min_budget_hint": 150000,
        })),
    )

    parser = GeminiProfileParser(api_key="test-key", model="gemini-2.0-flash")

    result = parser.parse("Senior Python backend developer")

    assert result == {
        "stack": ["fastapi", "postgresql"],
        "specialization": "backend",
        "level": "senior",
        "preferred_work_type": "remote",
        "min_budget_hint": 150000.0,
    }


@patch("urllib.request.urlopen")
def test_profile_parser_returns_none_on_invalid_json(mock_open) -> None:
    _mock_urlopen(mock_open, _gemini_response("not-json"))
    parser = GeminiProfileParser(api_key="test-key")

    assert parser.parse("Python developer") is None
