from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


class _FakeCrossEncoder:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._scores = [2.0, 0.0, -2.0]

    def predict(self, pairs, convert_to_numpy: bool = True, show_progress_bar: bool = False):  # noqa: ARG002
        return list(self._scores[: len(pairs)])


def _load_reranker_module():
    fake_mod = types.SimpleNamespace(
        SentenceTransformer=object,
        CrossEncoder=_FakeCrossEncoder,
    )
    sys.modules["sentence_transformers"] = fake_mod
    return importlib.reload(
        importlib.import_module("ai_service.adapter.sentence_transformers.reranker")
    )


def test_reranker_model_name_property() -> None:
    mod = _load_reranker_module()
    reranker = mod.CrossEncoderReranker("demo-reranker")
    assert reranker.model_name == "demo-reranker"


def test_score_pairs_returns_normalized_scores() -> None:
    mod = _load_reranker_module()
    reranker = mod.CrossEncoderReranker("demo-reranker")
    scores = reranker.score_pairs([("a", "b"), ("c", "d"), ("e", "f")])
    assert len(scores) == 3
    assert scores[0] > scores[1] > scores[2]
    assert scores[1] == pytest.approx(0.5)


def test_score_pairs_empty_returns_empty_list() -> None:
    mod = _load_reranker_module()
    reranker = mod.CrossEncoderReranker("demo-reranker")
    assert reranker.score_pairs([]) == []


def test_package_exports_reranker_class() -> None:
    _load_reranker_module()
    pkg = importlib.reload(importlib.import_module("ai_service.adapter.sentence_transformers"))
    assert "CrossEncoderReranker" in pkg.__all__


def test_prefers_bundled_rerank_model_dir_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_reranker_module()
    bundled_dir = tmp_path / "models"
    model_dir = bundled_dir / "BAAI" / "bge-reranker-base"
    model_dir.mkdir(parents=True)
    monkeypatch.setenv("RERANK_MODELS_DIR", str(bundled_dir))

    reranker = mod.CrossEncoderReranker("BAAI/bge-reranker-base")
    assert reranker._model.model_name == str(model_dir)  # type: ignore[attr-defined]


def test_raises_when_local_required_and_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_reranker_module()
    monkeypatch.setenv("RERANK_REQUIRE_LOCAL", "1")
    monkeypatch.setenv("RERANK_MODELS_DIR", "/tmp/not-existing-rerank-model-dir")

    with pytest.raises(ValueError):
        mod.CrossEncoderReranker("missing-reranker")
