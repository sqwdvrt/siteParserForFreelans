"""Adapter: cross-encoder reranker for user/job text pairs."""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from sentence_transformers import CrossEncoder

DEFAULT_MODEL = "BAAI/bge-reranker-base"
DEFAULT_BUNDLED_MODELS_DIR = "/opt/models"
MODELS_DIR_ENV = "RERANK_MODELS_DIR"
REQUIRE_LOCAL_ENV = "RERANK_REQUIRE_LOCAL"
FALLBACK_MODELS_DIR_ENV = "EMBEDDING_MODELS_DIR"
FALLBACK_REQUIRE_LOCAL_ENV = "EMBEDDING_REQUIRE_LOCAL"

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Cross-encoder reranker backed by sentence-transformers CrossEncoder."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        model_source, local_only = _resolve_model_source(model_name)
        try:
            self._model = CrossEncoder(
                model_source,
                local_files_only=local_only,
                automodel_args={"low_cpu_mem_usage": True},
            )
        except TypeError:
            try:
                self._model = CrossEncoder(model_source, local_files_only=local_only)
            except TypeError:
                # Fallback for test fakes that only accept one positional arg.
                self._model = CrossEncoder(model_source)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Return normalized rerank scores in 0..1 for query/document pairs."""
        if not pairs:
            return []
        raw_scores = self._model.predict(
            pairs,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        if hasattr(raw_scores, "tolist"):
            values = raw_scores.tolist()
        else:
            values = list(raw_scores)
        return [_sigmoid(float(value)) for value in values]


def _resolve_model_source(model_name: str) -> tuple[str, bool]:
    require_local = os.getenv(
        REQUIRE_LOCAL_ENV,
        os.getenv(FALLBACK_REQUIRE_LOCAL_ENV, "0"),
    ).lower() in {"1", "true", "yes", "on"}
    model_path = Path(model_name)
    if model_path.exists():
        logger.info("Using rerank model from path: %s", model_path)
        return str(model_path), True

    models_dir = Path(
        os.getenv(
            MODELS_DIR_ENV,
            os.getenv(FALLBACK_MODELS_DIR_ENV, DEFAULT_BUNDLED_MODELS_DIR),
        )
    )
    bundled_path = models_dir / model_name
    if bundled_path.exists():
        logger.info("Using bundled rerank model: %s", bundled_path)
        return str(bundled_path), True

    if require_local:
        raise ValueError(
            f"Rerank model '{model_name}' is not available locally. "
            f"Checked path '{model_path}' and bundled path '{bundled_path}'. "
            f"Set {REQUIRE_LOCAL_ENV}=0 to allow remote download."
        )

    logger.info("Using remote rerank model id: %s", model_name)
    return model_name, False


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)
