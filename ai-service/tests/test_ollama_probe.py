from __future__ import annotations

import json
from io import BytesIO

from ai_service.util.ollama_probe import probe_ollama


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.status = status
        self._stream = BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self._stream.read(*args, **kwargs)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        _ = exc_type
        _ = exc
        _ = tb
        return False


def test_probe_ollama_accepts_available_required_models(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _FakeResponse(
            {"models": [{"name": "llama3.2:3b-instruct-q4_K_M"}]}
        ),
    )

    assert probe_ollama(
        "http://ollama:11434",
        required=True,
        required_models=["llama3.2:3b-instruct-q4_K_M"],
    )


def test_probe_ollama_rejects_missing_required_models(monkeypatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _FakeResponse({"models": [{"name": "mistral:7b"}]}),
    )

    assert not probe_ollama(
        "http://ollama:11434",
        required=True,
        required_models=["llama3.2:3b-instruct-q4_K_M"],
    )
