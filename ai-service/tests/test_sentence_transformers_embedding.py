from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


class _FakeVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return list(self._values)


class _FakeSentenceTransformer:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._values = [0.1] * 384

    def encode(self, text: str, convert_to_numpy: bool = True):  # noqa: ARG002
        return _FakeVector(self._values)


def _load_embedding_module():
    fake_mod = types.SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer)
    sys.modules["sentence_transformers"] = fake_mod
    return importlib.reload(
        importlib.import_module("ai_service.adapter.sentence_transformers.embedding")
    )


def test_embedding_model_name_property() -> None:
    mod = _load_embedding_module()
    emb = mod.SentenceTransformerEmbedding("demo-model")
    assert emb.model_name == "demo-model"


def test_encode_empty_text_returns_zero_vector() -> None:
    mod = _load_embedding_module()
    emb = mod.SentenceTransformerEmbedding("demo-model")
    vec = emb.encode("   ")
    assert len(vec) == mod.EMBEDDING_DIM
    assert all(x == 0.0 for x in vec)


def test_encode_non_empty_returns_vector() -> None:
    mod = _load_embedding_module()
    emb = mod.SentenceTransformerEmbedding("demo-model")
    vec = emb.encode("python backend")
    assert len(vec) == mod.EMBEDDING_DIM
    assert vec[0] == pytest.approx(0.1)


def test_encode_raises_on_dimension_mismatch() -> None:
    mod = _load_embedding_module()
    emb = mod.SentenceTransformerEmbedding("demo-model")
    emb._model._values = [0.2] * 10  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        emb.encode("x")


def test_package_exports_embedding_class() -> None:
    _load_embedding_module()
    pkg = importlib.reload(importlib.import_module("ai_service.adapter.sentence_transformers"))
    assert "SentenceTransformerEmbedding" in pkg.__all__


def test_prefers_bundled_model_dir_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_embedding_module()
    bundled_dir = tmp_path / "models"
    model_dir = bundled_dir / "demo-model"
    model_dir.mkdir(parents=True)
    monkeypatch.setenv("EMBEDDING_MODELS_DIR", str(bundled_dir))

    emb = mod.SentenceTransformerEmbedding("demo-model")
    assert emb._model.model_name == str(model_dir)  # type: ignore[attr-defined]


def test_raises_when_local_required_and_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_embedding_module()
    monkeypatch.setenv("EMBEDDING_REQUIRE_LOCAL", "1")
    monkeypatch.setenv("EMBEDDING_MODELS_DIR", "/tmp/not-existing-model-dir")

    with pytest.raises(ValueError):
        mod.SentenceTransformerEmbedding("missing-model")


def test_allows_remote_when_local_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_embedding_module()
    monkeypatch.delenv("EMBEDDING_REQUIRE_LOCAL", raising=False)
    monkeypatch.setenv("EMBEDDING_MODELS_DIR", "/tmp/not-existing-model-dir")

    emb = mod.SentenceTransformerEmbedding("remote-model")
    assert emb._model.model_name == "remote-model"  # type: ignore[attr-defined]
