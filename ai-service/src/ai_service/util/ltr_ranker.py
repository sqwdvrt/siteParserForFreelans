import os
import json
import logging
import pickle
from pathlib import Path
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class LTRFeatures:
    """Features for the LTR model."""

    cosine_similarity: float       # user-job embedding similarity
    rerank_score: float            # cross-encoder rerank score
    feedback_net: float            # net feedback signal (-1 to 1)
    time_decay: float              # exp(-hours / 48)
    competition_factor: float      # 1 / (1 + bids/50)
    tag_affinity: float            # 0-1 tag match score
    preference_bonus: float        # 1.2 for preferred, 0.8 otherwise

    def to_array(self) -> np.ndarray:
        return np.array(
            [
                self.cosine_similarity,
                self.rerank_score,
                self.feedback_net,
                self.time_decay,
                self.competition_factor,
                self.tag_affinity,
                self.preference_bonus,
            ],
            dtype=np.float32,
        )

    @staticmethod
    def feature_names() -> list[str]:
        return [
            "cosine_similarity",
            "rerank_score",
            "feedback_net",
            "time_decay",
            "competition_factor",
            "tag_affinity",
            "preference_bonus",
        ]


class LTRRanker:
    """LightGBM-based ranker with fallback to manual formula."""

    def __init__(self, model_path: str | None = None):
        self._model = None
        self._metadata = None
        self._fallback_enabled = True

        if model_path:
            self._load_model(model_path)

    def _load_model(self, model_path: str):
        """Load LightGBM model from file."""
        try:
            import lightgbm as lgb

            path = Path(model_path)
            if path.exists():
                self._model = lgb.Booster(model_file=str(path))

                # Load metadata if exists
                meta_path = path.with_suffix(".json")
                if meta_path.exists():
                    self._metadata = json.loads(meta_path.read_text())

                logger.info("LTR model loaded from %s", model_path)
            else:
                logger.warning(
                    "LTR model path not found: %s, using fallback", model_path
                )
        except ImportError:
            logger.error("lightgbm not installed, using fallback")
        except Exception as e:
            logger.error("Failed to load LTR model: %s, using fallback", e)

    def predict_score(self, features: LTRFeatures) -> float:
        """Predict relevance score. Falls back to manual formula if model unavailable."""
        if self._model is not None:
            try:
                import lightgbm as lgb

                feature_array = features.to_array().reshape(1, -1)
                score = self._model.predict(feature_array)[0]
                return float(score)
            except Exception as e:
                logger.warning("LTR prediction failed, using fallback: %s", e)

        return self._fallback_score(features)

    def _fallback_score(self, features: LTRFeatures) -> float:
        """Manual scoring formula (current production formula)."""
        # Base: rerank score with feedback adjustment
        base_score = features.rerank_score

        # Feedback adjustment (same as current production)
        feedback_multiplier = 1.0
        if abs(features.feedback_net) > 0.20:  # dead zone
            if features.feedback_net > 0:
                feedback_multiplier = min(1.15, 1 + features.feedback_net * 0.15)
            else:
                feedback_multiplier = max(0.50, 1 + features.feedback_net * 0.50)

        score = base_score * feedback_multiplier

        # Time decay
        score *= features.time_decay

        # Competition
        score *= features.competition_factor

        # Preference
        score *= features.preference_bonus

        # Tag affinity bonus (+5% per point, max +15%)
        score *= 1 + features.tag_affinity * 0.15

        return float(score)

    def is_available(self) -> bool:
        return self._model is not None

    def get_metadata(self) -> dict | None:
        return self._metadata


# Global instance
_ranker: LTRRanker | None = None


def get_ltr_ranker() -> LTRRanker:
    global _ranker
    if _ranker is None:
        model_path = os.getenv("LTR_MODEL_PATH")
        _ranker = LTRRanker(model_path)
    return _ranker


def reset_ltr_ranker():
    """For testing."""
    global _ranker
    _ranker = None
