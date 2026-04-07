#!/usr/bin/env python
"""Fine-tune a SentenceTransformer model using TripletLoss.

Usage examples
--------------
# Basic fine-tuning
python scripts/fine_tune_embedding_model.py --triplets-file triplets.json

# Custom base model and hyperparameters
python scripts/fine_tune_embedding_model.py \\
    --triplets-file triplets.json \\
    --base-model sentence-transformers/all-mpnet-base-v2 \\
    --epochs 5 --batch-size 32 --learning-rate 1e-5

# Dry-run to inspect data without training
python scripts/fine_tune_embedding_model.py --triplets-file triplets.json --dry-run

# Train and evaluate
python scripts/fine_tune_embedding_model.py --triplets-file triplets.json --evaluate
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
from sentence_transformers import InputExample, SentenceTransformer, losses
from sentence_transformers.evaluation import TripletEvaluator
from torch.utils.data import DataLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fine_tune")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_BASE_MODEL = "all-MiniLM-L6-v2"
DEFAULT_EPOCHS = 3
DEFAULT_BATCH_SIZE = 16
DEFAULT_LEARNING_RATE = 2e-5
DEFAULT_OUTPUT_DIR = "models/fine-tuned"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two dense vectors."""
    va = torch.tensor(a, dtype=torch.float32)
    vb = torch.tensor(b, dtype=torch.float32)
    return float(torch.nn.functional.cosine_similarity(va, vb), dim=0)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_triplets(path: str | Path) -> list[dict[str, Any]]:
    """Load triplets from a JSON file.

    Expected JSON structure: a list of objects, each with keys:
        - anchor  (str): user profile / query text
        - positive (str): good job description text
        - negative (str): bad job description text
    The file may also contain metadata fields which are ignored here.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        # Some exporters wrap the list under a "triplets" key.
        data = data.get("triplets", data.get("data", [data]))

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON list of triplets, got {type(data).__name__}. "
            "File format should be: [{\"anchor\":..., \"positive\":..., \"negative\":...}, ...]"
        )

    required = {"anchor", "positive", "negative"}
    validated: list[dict[str, Any]] = []
    for idx, item in enumerate(data):
        missing = required - set(item.keys())
        if missing:
            logger.warning("Skipping triplet %d: missing keys %s", idx, missing)
            continue
        validated.append(item)

    logger.info("Loaded %d valid triplets from %s", len(validated), path)
    return validated


def build_contrastive_dataset(
    triplets: list[dict[str, Any]],
) -> list[InputExample]:
    """Convert raw triplet dicts into SentenceTransformer InputExample list."""
    examples: list[InputExample] = []
    for item in triplets:
        examples.append(
            InputExample(
                texts=[
                    str(item["anchor"]),
                    str(item["positive"]),
                    str(item["negative"]),
                ]
            )
        )
    return examples


def split_train_test(
    examples: list[InputExample],
    test_ratio: float = 0.1,
) -> tuple[list[InputExample], list[InputExample]]:
    """Deterministic train/test split (no shuffle to keep reproducibility)."""
    split = int(len(examples) * (1 - test_ratio))
    return examples[:split], examples[split:]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_model(
    triplets: list[dict[str, Any]],
    base_model: str,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    output_dir: str,
) -> dict[str, Any]:
    """Run the fine-tuning loop and return training stats."""
    logger.info("Loading base model: %s", base_model)
    model = SentenceTransformer(base_model)

    examples = build_contrastive_dataset(triplets)
    train_examples, test_examples = split_train_test(examples, test_ratio=0.1)

    train_dataloader = DataLoader(
        train_examples,
        batch_size=batch_size,
        shuffle=True,
        drop_last=False,
    )

    # Number of training steps per epoch for warmup calculation
    steps_per_epoch = len(train_dataloader)
    warmup_steps = max(1, int(steps_per_epoch * epochs * 0.1))

    loss_fn = losses.TripletLoss(model=model)

    # Optional evaluator on the held-out test set
    evaluator = None
    if test_examples:
        test_anchors = [ex.texts[0] for ex in test_examples]
        test_positives = [ex.texts[1] for ex in test_examples]
        test_negatives = [ex.texts[2] for ex in test_examples]
        evaluator = TripletEvaluator(
            anchors=test_anchors,
            positives=test_positives,
            negatives=test_negatives,
            name="test",
        )

    model_name_short = base_model.replace("/", "_").replace(" ", "_")
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    model_output_path = os.path.join(output_dir, f"{model_name_short}_{date_str}")
    os.makedirs(model_output_path, exist_ok=True)

    logger.info("Training for %d epochs, batch_size=%d, lr=%.1e, warmup=%d",
                epochs, batch_size, learning_rate, warmup_steps)
    logger.info("Train examples: %d, Test examples: %d",
                len(train_examples), len(test_examples))
    logger.info("Output directory: %s", model_output_path)

    model.fit(
        train_objectives=[(train_dataloader, loss_fn)],
        evaluator=evaluator,
        epochs=epochs,
        warmup_steps=warmup_steps,
        output_path=model_output_path,
        save_best_model=True,
        optimizer_params={"lr": learning_rate},
        show_progress_bar=True,
    )

    logger.info("Training complete. Model saved to %s", model_output_path)
    return {
        "output_path": model_output_path,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "warmup_steps": warmup_steps,
        "train_examples": len(train_examples),
        "test_examples": len(test_examples),
        "steps_per_epoch": steps_per_epoch,
    }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_model(
    triplets: list[dict[str, Any]],
    finetuned_path: str,
    base_model_name: str,
) -> dict[str, Any]:
    """Compare base vs fine-tuned model on the full triplet set.

    For each triplet, compute cosine(anchor, positive) and cosine(anchor, negative).
    Accuracy = fraction where cosine(anchor, positive) > cosine(anchor, negative).
    """
    logger.info("Loading base model for evaluation: %s", base_model_name)
    base_model = SentenceTransformer(base_model_name)

    logger.info("Loading fine-tuned model for evaluation: %s", finetuned_path)
    ft_model = SentenceTransformer(finetuned_path)

    base_correct = 0
    ft_correct = 0
    total = len(triplets)

    for idx, triplet in enumerate(triplets):
        anchor = str(triplet["anchor"])
        positive = str(triplet["positive"])
        negative = str(triplet["negative"])

        # Base model
        emb_base = base_model.encode([anchor, positive, negative], convert_to_numpy=True)
        base_cos_pos = float(torch.nn.functional.cosine_similarity(
            torch.tensor(emb_base[0]), torch.tensor(emb_base[1])
        ))
        base_cos_neg = float(torch.nn.functional.cosine_similarity(
            torch.tensor(emb_base[0]), torch.tensor(emb_base[2])
        ))
        if base_cos_pos > base_cos_neg:
            base_correct += 1

        # Fine-tuned model
        emb_ft = ft_model.encode([anchor, positive, negative], convert_to_numpy=True)
        ft_cos_pos = float(torch.nn.functional.cosine_similarity(
            torch.tensor(emb_ft[0]), torch.tensor(emb_ft[1])
        ))
        ft_cos_neg = float(torch.nn.functional.cosine_similarity(
            torch.tensor(emb_ft[0]), torch.tensor(emb_ft[2])
        ))
        if ft_cos_pos > ft_cos_neg:
            ft_correct += 1

        if (idx + 1) % 500 == 0:
            logger.info("Evaluated %d / %d triplets", idx + 1, total)

    base_accuracy = base_correct / max(total, 1)
    ft_accuracy = ft_correct / max(total, 1)

    results = {
        "total_triplets": total,
        "base_model": {
            "name": base_model_name,
            "correct": base_correct,
            "accuracy": round(base_accuracy, 4),
        },
        "finetuned_model": {
            "path": finetuned_path,
            "correct": ft_correct,
            "accuracy": round(ft_accuracy, 4),
        },
        "improvement": round(ft_accuracy - base_accuracy, 4),
    }

    logger.info("=" * 60)
    logger.info("EVALUATION RESULTS")
    logger.info("=" * 60)
    logger.info("Total triplets evaluated: %d", total)
    logger.info("Base model accuracy:      %.4f (%d / %d)", base_accuracy, base_correct, total)
    logger.info("Fine-tuned accuracy:      %.4f (%d / %d)", ft_accuracy, ft_correct, total)
    logger.info("Improvement:              %+.4f", ft_accuracy - base_accuracy)
    logger.info("=" * 60)

    return results


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def save_manifest(
    output_path: str,
    base_model_name: str,
    training_stats: dict[str, Any],
    evaluation_results: dict[str, Any] | None,
    triplet_count: int,
) -> str:
    """Write manifest.json into the model output directory."""
    manifest = {
        "base_model_name": base_model_name,
        "training_date": _now_iso(),
        "training_stats": {
            "epochs": training_stats.get("epochs"),
            "batch_size": training_stats.get("batch_size"),
            "learning_rate": training_stats.get("learning_rate"),
            "warmup_steps": training_stats.get("warmup_steps"),
            "train_examples": training_stats.get("train_examples"),
            "test_examples": training_stats.get("test_examples"),
            "steps_per_epoch": training_stats.get("steps_per_epoch"),
        },
        "evaluation_results": evaluation_results,
        "triplet_count": triplet_count,
        "model_format": "sentence-transformers",
    }

    manifest_path = os.path.join(output_path, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info("Manifest saved to %s", manifest_path)
    return manifest_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fine-tune a SentenceTransformer embedding model with TripletLoss.",
    )
    parser.add_argument(
        "--triplets-file",
        required=True,
        help="Path to JSON file containing training triplets.",
    )
    parser.add_argument(
        "--base-model",
        default=DEFAULT_BASE_MODEL,
        help=f"Base SentenceTransformer model (default: {DEFAULT_BASE_MODEL}).",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=DEFAULT_EPOCHS,
        help=f"Number of training epochs (default: {DEFAULT_EPOCHS}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Training batch size (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--learning-rate",
        type=float,
        default=DEFAULT_LEARNING_RATE,
        help=f"Learning rate (default: {DEFAULT_LEARNING_RATE}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save the fine-tuned model (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        default=False,
        help="Run evaluation comparing base vs fine-tuned model after training.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print dataset statistics without performing training.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Resolve output dir relative to the ai-service root so that
    # `models/fine-tuned` lands inside the ai-service directory.
    script_dir = Path(__file__).resolve().parent
    ai_service_root = script_dir.parent
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ai_service_root / output_dir
    output_dir = str(output_dir)

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    logger.info("Loading triplets from %s", args.triplets_file)
    triplets = load_triplets(args.triplets_file)

    if not triplets:
        logger.error("No valid triplets found. Aborting.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Dry-run mode
    # ------------------------------------------------------------------
    if args.dry_run:
        examples = build_contrastive_dataset(triplets)
        train_ex, test_ex = split_train_test(examples, test_ratio=0.1)
        print("\n" + "=" * 60)
        print("DRY RUN -- Dataset Statistics")
        print("=" * 60)
        print(f"  Total triplets:       {len(triplets)}")
        print(f"  Training examples:    {len(train_ex)}")
        print(f"  Test examples:        {len(test_ex)}")
        print(f"  Base model:           {args.base_model}")
        print(f"  Epochs:               {args.epochs}")
        print(f"  Batch size:           {args.batch_size}")
        print(f"  Learning rate:        {args.learning_rate}")
        print(f"  Warmup steps:         ~{max(1, int(len(train_ex) / max(args.batch_size, 1) * args.epochs * 0.1))}")
        print(f"  Output directory:     {output_dir}")
        print("=" * 60 + "\n")
        return

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------
    training_stats = train_model(
        triplets=triplets,
        base_model=args.base_model,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        output_dir=output_dir,
    )

    output_path = training_stats["output_path"]

    # ------------------------------------------------------------------
    # Evaluation (optional)
    # ------------------------------------------------------------------
    evaluation_results = None
    if args.evaluate:
        evaluation_results = evaluate_model(
            triplets=triplets,
            finetuned_path=output_path,
            base_model_name=args.base_model,
        )

    # ------------------------------------------------------------------
    # Save manifest
    # ------------------------------------------------------------------
    save_manifest(
        output_path=output_path,
        base_model_name=args.base_model,
        training_stats=training_stats,
        evaluation_results=evaluation_results,
        triplet_count=len(triplets),
    )

    # Save evaluation results separately if evaluation was run
    if evaluation_results is not None:
        eval_file = os.path.join(output_path, "evaluation_results.json")
        with open(eval_file, "w", encoding="utf-8") as f:
            json.dump(evaluation_results, f, indent=2, ensure_ascii=False)
        logger.info("Evaluation results saved to %s", eval_file)

    logger.info("Fine-tuning pipeline complete.")


if __name__ == "__main__":
    main()
