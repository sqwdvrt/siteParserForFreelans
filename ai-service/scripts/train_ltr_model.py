#!/usr/bin/env python
"""Train LightGBM ranker from historical feedback data.

Usage:
    python scripts/train_ltr_model.py --output models/ltr/
"""
import argparse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ltr_train")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Output directory for model")
    parser.add_argument("--feedback-file", help="JSON file with feedback pairs")
    parser.add_argument("--n-estimators", type=int, default=100)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.parse_args()

    try:
        import lightgbm as lgb  # noqa: F401
        from sklearn.metrics import ndcg_score  # noqa: F401
        from sklearn.model_selection import train_test_split  # noqa: F401
    except ImportError:
        logger.error("Required packages: lightgbm, scikit-learn")
        return

    # TODO: Load training data from feedback-file
    # Format: list of {user_features: [...], relevance: 0|1}
    # For now, placeholder

    logger.info("Training data loading not yet implemented")
    logger.info("This script will be completed when feedback export is ready")


if __name__ == "__main__":
    main()
