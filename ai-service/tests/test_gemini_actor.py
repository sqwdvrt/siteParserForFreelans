"""Tests for GeminiActorAgent."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ai_service.adapter.gemini import GeminiActorAgent
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


def _gemini_response(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _mock_urlopen(mock_open, payload: dict) -> None:
    mock_resp = mock_open.return_value.__enter__.return_value
    mock_resp.read.return_value = json.dumps(payload).encode()


@patch("urllib.request.urlopen")
def test_valid_response_returns_ranked_jobs(mock_open) -> None:
    _mock_urlopen(
        mock_open,
        _gemini_response(json.dumps({
            "selected": [
                {"job_id": 1, "rank": 1, "why_it_fits": "Python match", "confidence": 0.9},
                {"job_id": 2, "rank": 2, "why_it_fits": "Django exp", "confidence": 0.7},
            ]
        })),
    )
    actor = GeminiActorAgent(api_key="test-key", model="gemini-1.5-flash")

    result = actor.select(_user(), _jobs())

    assert len(result) == 2
    assert result[0].job_id == 1
    assert result[0].rank == 1
    assert result[1].job_id == 2
    assert result[1].rank == 2


@patch("urllib.request.urlopen")
def test_invalid_json_returns_empty(mock_open) -> None:
    _mock_urlopen(mock_open, _gemini_response("not json"))
    actor = GeminiActorAgent(api_key="test-key", model="gemini-1.5-flash")

    result = actor.select(_user(), _jobs()[:1])

    assert result == []


@patch("urllib.request.urlopen")
def test_unknown_job_id_in_response_is_skipped(mock_open) -> None:
    _mock_urlopen(
        mock_open,
        _gemini_response(json.dumps({"selected": [{"job_id": 999, "rank": 1, "why_it_fits": "x"}]})),
    )
    actor = GeminiActorAgent(api_key="test-key", model="gemini-1.5-flash")

    result = actor.select(_user(), _jobs()[:1])

    assert result == []


@patch("urllib.request.urlopen")
def test_empty_candidates_returns_empty_string(mock_open) -> None:
    _mock_urlopen(mock_open, {"candidates": []})
    actor = GeminiActorAgent(api_key="test-key", model="gemini-1.5-flash")

    result = actor.select(_user(), _jobs()[:1])

    assert result == []


@patch("urllib.request.urlopen")
def test_timeout_propagates(mock_open) -> None:
    mock_open.side_effect = TimeoutError("timed out")
    actor = GeminiActorAgent(api_key="test-key", model="gemini-1.5-flash")

    with pytest.raises(TimeoutError):
        actor.select(_user(), _jobs()[:1])


@patch("urllib.request.urlopen")
def test_circuit_breaker_skips_repeated_timeouts(mock_open) -> None:
    mock_open.side_effect = TimeoutError("timed out")
    actor = GeminiActorAgent(
        api_key="test-key",
        model="gemini-1.5-flash",
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


def test_empty_jobs_returns_empty_without_api_call() -> None:
    actor = GeminiActorAgent(api_key="test-key")

    result = actor.select(_user(), [])

    assert result == []


@patch("urllib.request.urlopen")
def test_explain_batch_returns_explanations_in_order(mock_open) -> None:
    _mock_urlopen(
        mock_open,
        _gemini_response(json.dumps({"explanations": ["Python match", "Django match"]})),
    )
    actor = GeminiActorAgent(api_key="test-key", model="gemini-1.5-flash")

    result = actor.explain_batch(_user(), _jobs()[:2])

    assert result == ["Python match", "Django match"]


@patch("urllib.request.urlopen")
def test_explain_batch_invalid_length_returns_empty(mock_open) -> None:
    _mock_urlopen(
        mock_open,
        _gemini_response(json.dumps({"explanations": ["only one"]})),
    )
    actor = GeminiActorAgent(api_key="test-key", model="gemini-1.5-flash")

    result = actor.explain_batch(_user(), _jobs()[:2])

    assert result == []


# --- 429 retry tests ---


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    hdrs = MagicMock()
    hdrs.get = lambda k, d=None: retry_after if k == "Retry-After" else d
    return urllib.error.HTTPError(url="http://x", code=code, msg=str(code), hdrs=hdrs, fp=None)


def _success_resp(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(payload).encode()
    return resp


@patch("urllib.request.urlopen")
@patch("time.sleep")
def test_explain_batch_retries_on_429_then_succeeds(mock_sleep, mock_open) -> None:
    """429 на первой попытке → retry → успех."""
    mock_open.side_effect = [
        _http_error(429),
        _success_resp(_gemini_response(json.dumps({"explanations": ["Good fit"]}))),
    ]
    actor = GeminiActorAgent(api_key="k", model="m", max_retries=2, base_delay_sec=1.0)

    result = actor.explain_batch(_user(), _jobs()[:1])

    assert result == ["Good fit"]
    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_once_with(1.0)  # base_delay_sec * 2^0


@patch("urllib.request.urlopen")
@patch("time.sleep")
def test_explain_batch_raises_after_max_retries(mock_sleep, mock_open) -> None:
    """Все retries исчерпаны → исключение пробрасывается."""
    mock_open.side_effect = _http_error(429)
    actor = GeminiActorAgent(api_key="k", model="m", max_retries=2, base_delay_sec=1.0)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        actor.explain_batch(_user(), _jobs()[:1])

    assert exc_info.value.code == 429
    assert mock_sleep.call_count == 2  # retry 1 и retry 2


@patch("urllib.request.urlopen")
@patch("time.sleep")
def test_non_429_http_error_not_retried(mock_sleep, mock_open) -> None:
    """HTTP 500 не ретраится."""
    mock_open.side_effect = _http_error(500)
    actor = GeminiActorAgent(api_key="k", model="m", max_retries=2, base_delay_sec=1.0)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        actor.select(_user(), _jobs()[:1])

    assert exc_info.value.code == 500
    mock_sleep.assert_not_called()


@patch("urllib.request.urlopen")
@patch("time.sleep")
def test_retry_uses_retry_after_header_if_present(mock_sleep, mock_open) -> None:
    """Если Gemini вернул Retry-After: 30, sleep должен быть 30.0."""
    mock_open.side_effect = [
        _http_error(429, retry_after="30"),
        _success_resp(_gemini_response(json.dumps({"explanations": ["fit"]}))),
    ]
    actor = GeminiActorAgent(api_key="k", model="m", max_retries=2, base_delay_sec=1.0)

    actor.explain_batch(_user(), _jobs()[:1])

    mock_sleep.assert_called_once_with(30.0)


@patch("urllib.request.urlopen")
@patch("time.sleep")
def test_retry_exponential_backoff_delay(mock_sleep, mock_open) -> None:
    """Второй retry ждёт base * 2^1 = 2.0."""
    mock_open.side_effect = [
        _http_error(429),
        _http_error(429),
        _success_resp(_gemini_response(json.dumps({"explanations": ["fit"]}))),
    ]
    actor = GeminiActorAgent(api_key="k", model="m", max_retries=2, base_delay_sec=1.0)

    actor.explain_batch(_user(), _jobs()[:1])

    assert mock_sleep.call_count == 2
    calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert calls == [1.0, 2.0]  # base * 2^0, base * 2^1
