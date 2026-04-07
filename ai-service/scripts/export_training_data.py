#!/usr/bin/env python3
"""
Export Training Data — CLI скрипт для экспорта триплетов из PostgreSQL.

Использование:
    python scripts/export_training_data.py --days 30 --output training_data/
"""
import asyncio
import os
import sys
from pathlib import Path

# Добавляем src в path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_service.adapter.postgres.training_data_exporter import (
    TrainingDataExporter,
    save_training_data,
)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Export training triplets from PostgreSQL")
    parser.add_argument("--days", type=int, default=30, help="Days of feedback to include (default: 30)")
    parser.add_argument("--output", type=str, default="training_data", help="Output directory")
    parser.add_argument("--min-feedback", type=int, default=1, help="Minimum feedback count per user")
    parser.add_argument("--max-triplets-per-user", type=int, default=10, help="Max triplets per user")
    parser.add_argument("--dsn", type=str, help="PostgreSQL DSN (or set DATABASE_URL env var)")

    args = parser.parse_args()

    dsn = args.dsn or os.getenv("DATABASE_URL")
    if not dsn:
        print("ERROR: Set --dsn or DATABASE_URL environment variable", file=sys.stderr)
        sys.exit(1)

    print("Connecting to database...")
    exporter = TrainingDataExporter(dsn)

    print(f"Exporting triplets (last {args.days} days)...")
    triplets = await exporter.export_triplets(
        days=args.days,
        min_feedback=args.min_feedback,
        max_triplets_per_user=args.max_triplets_per_user,
    )

    print("Exporting evaluation pairs...")
    eval_pairs = await exporter.export_eval_pairs(days=args.days)

    print(f"Saving to {args.output}/...")
    stats = save_training_data(triplets, eval_pairs, args.output)

    # Печатаем статистику
    print("\n" + "=" * 60)
    print("Training Data Statistics")
    print("=" * 60)
    print(f"Triplets exported:        {stats['triplets_count']}")
    print(f"Eval pairs exported:      {stats['eval_pairs_count']}")
    print(f"Good feedback:            {stats['good_feedback']}")
    print(f"Bad feedback:             {stats['bad_feedback']}")
    print(f"Good/Bad ratio:           {stats['good_ratio']:.2f}")
    print(f"Date:                     {stats['date']}")
    print(f"Triplets file:            {stats['triplets_file']}")
    print(f"Eval pairs file:          {stats['eval_file']}")
    print("=" * 60)

    if stats["triplets_count"] == 0:
        print("\nWARNING: No triplets exported. Check if you have feedback data.")
        sys.exit(1)

    if stats["good_ratio"] < 0.3 or stats["good_ratio"] > 0.7:
        print(f"\nWARNING: Unbalanced dataset (good_ratio={stats['good_ratio']:.2f})")
        print("Consider adjusting the time window or collecting more feedback.")


if __name__ == "__main__":
    asyncio.run(main())
