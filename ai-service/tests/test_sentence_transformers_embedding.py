from __future__ import annotations

import importlib
import sys
import types

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
