"""Adapter: SentenceTransformer для encode(text) -> vector 384."""

import logging
import os
from pathlib import Path

from sentence_transformers import SentenceTransformer

from ai_service.domain.embedding import EMBEDDING_DIM
from ai_service.port.embedding import EmbeddingService

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_BUNDLED_MODELS_DIR = "/opt/models"
MODELS_DIR_ENV = "EMBEDDING_MODELS_DIR"
REQUIRE_LOCAL_ENV = "EMBEDDING_REQUIRE_LOCAL"

logger = logging.getLogger(__name__)


class SentenceTransformerEmbedding(EmbeddingService):
    """Embedding через sentence-transformers."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        model_source, local_only = _resolve_model_source(model_name)
        try:
            # Keep initialization peak memory lower on small containers.
            self._model = SentenceTransformer(
                model_source,
                local_files_only=local_only,
                model_kwargs={"low_cpu_mem_usage": True},
            )
        except TypeError:
            try:
                # Backward-compatible fallback for older/newer ST signatures.
                self._model = SentenceTransformer(model_source, local_files_only=local_only)
            except TypeError:
                # Fallback for test fakes that only accept one positional arg.
                self._model = SentenceTransformer(model_source)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def encode(self, text: str) -> list[float]:
        if not text or not text.strip():
            return [0.0] * EMBEDDING_DIM
        embedding = self._model.encode(text, convert_to_numpy=True)
        vec = embedding.tolist()
        if len(vec) != EMBEDDING_DIM:
            raise ValueError(f"expected {EMBEDDING_DIM}, got {len(vec)}")
        return vec


def _resolve_model_source(model_name: str) -> tuple[str, bool]:
    require_local = os.getenv(REQUIRE_LOCAL_ENV, "0").lower() in {"1", "true", "yes", "on"}
    model_path = Path(model_name)
    if model_path.exists():
        logger.info("Using embedding model from path: %s", model_path)
        return str(model_path), True

    models_dir = Path(os.getenv(MODELS_DIR_ENV, DEFAULT_BUNDLED_MODELS_DIR))
    bundled_path = models_dir / model_name
    if bundled_path.exists():
        logger.info("Using bundled embedding model: %s", bundled_path)
        return str(bundled_path), True

    if require_local:
        raise ValueError(
            f"Embedding model '{model_name}' is not available locally. "
            f"Checked path '{model_path}' and bundled path '{bundled_path}'. "
            f"Set {REQUIRE_LOCAL_ENV}=0 to allow remote download."
        )

    logger.info("Using remote embedding model id: %s", model_name)
    return model_name, False
