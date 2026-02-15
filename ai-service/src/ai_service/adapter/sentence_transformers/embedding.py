"""Adapter: SentenceTransformer для encode(text) -> vector 384."""

from sentence_transformers import SentenceTransformer

from ai_service.port.embedding import EmbeddingService

DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384


class SentenceTransformerEmbedding(EmbeddingService):
    """Embedding через sentence-transformers."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self._model = SentenceTransformer(model_name)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def encode(self, text: str) -> list[float]:
        if not text or not text.strip():
            return [0.0] * EMBEDDING_DIM
        embedding = self._model.encode(text, convert_to_numpy=True)
        vec = embedding.tolist()
        assert len(vec) == EMBEDDING_DIM, f"expected {EMBEDDING_DIM}, got {len(vec)}"
        return vec
