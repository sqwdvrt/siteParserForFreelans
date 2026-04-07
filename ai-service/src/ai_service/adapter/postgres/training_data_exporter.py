"""
Training Data Exporter — экспортирует триплеты (user, good_job, bad_job) из PostgreSQL.

Используется для fine-tuning SentenceTransformer модели через TripletLoss.
"""
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg


@dataclass
class TrainingTriplet:
    anchor: str  # user profile text
    positive: str  # good job text
    negative: str  # bad job text
    user_id: int
    good_job_id: int
    bad_job_id: int


@dataclass
class EvalPair:
    user_id: int
    job_id: int
    feedback: str  # 'good' or 'bad'
    user_text: str
    job_text: str


class TrainingDataExporter:
    """Экспортирует training данные из PostgreSQL для contrastive learning."""

    def __init__(self, dsn: str):
        """
        Args:
            dsn: PostgreSQL connection string
        """
        self._dsn = dsn

    async def export_triplets(
        self,
        days: int = 30,
        min_feedback: int = 1,
        max_triplets_per_user: int = 10,
    ) -> list[TrainingTriplet]:
        """
        Экспортирует триплеты (anchor=user, positive=good_job, negative=bad_job).

        Args:
            days: временное окно для feedback (дней)
            min_feedback: минимальное кол-во feedback на пользователя
            max_triplets_per_user: макс. триплетов на одного пользователя

        Returns:
            Список TrainingTriplet
        """
        conn = await asyncpg.connect(self._dsn)
        try:
            return await self._build_triplets(
                conn,
                cutoff_date=datetime.utcnow() - timedelta(days=days),
                min_feedback=min_feedback,
                max_per_user=max_triplets_per_user,
            )
        finally:
            await conn.close()

    async def export_eval_pairs(
        self,
        days: int = 30,
    ) -> list[EvalPair]:
        """
        Экспортирует пары (user, job) с feedback для оценки качества.

        Returns:
            Список EvalPair
        """
        conn = await asyncpg.connect(self._dsn)
        try:
            return await self._build_eval_pairs(
                conn,
                cutoff_date=datetime.utcnow() - timedelta(days=days),
            )
        finally:
            await conn.close()

    async def _build_triplets(
        self,
        conn: asyncpg.Connection,
        cutoff_date: datetime,
        min_feedback: int,
        max_per_user: int,
    ) -> list[TrainingTriplet]:
        """Строит триплеты из feedback данных."""

        # 1. Получаем пользователей с profile_text и feedback
        users_with_feedback = await conn.fetch("""
            SELECT DISTINCT
                u.id AS user_id,
                u.profile_text,
                u.embedding
            FROM users u
            INNER JOIN user_feedback uf ON uf.user_id = u.id
            WHERE uf.created_at >= $1
              AND u.profile_text IS NOT NULL
              AND u.profile_text != ''
              AND u.embedding IS NOT NULL
        """, cutoff_date)

        if not users_with_feedback:
            return []

        triplets: list[TrainingTriplet] = []

        for user_row in users_with_feedback:
            user_id = user_row["user_id"]
            user_text = user_row["profile_text"]

            # 2. Получаем good и bad jobs для пользователя
            feedback_rows = await conn.fetch("""
                SELECT
                    uf.job_id,
                    uf.feedback,
                    j.title,
                    j.description,
                    j.source,
                    COALESCE(j.skills, ARRAY[]::text[]) AS skills
                FROM user_feedback uf
                INNER JOIN jobs j ON j.id = uf.job_id
                WHERE uf.user_id = $1
                  AND uf.created_at >= $2
                ORDER BY uf.created_at DESC
            """, user_id, cutoff_date)

            if len(feedback_rows) < min_feedback:
                continue

            good_jobs = [r for r in feedback_rows if r["feedback"] == "good"]
            bad_jobs = [r for r in feedback_rows if r["feedback"] == "bad"]

            if not good_jobs or not bad_jobs:
                continue

            # 3. Генерируем триплеты
            for good_job in good_jobs[:max_per_user]:
                for bad_job in bad_jobs[:max_per_user]:
                    if len(triplets) >= max_per_user:
                        break

                    triplets.append(TrainingTriplet(
                        anchor=user_text.strip(),
                        positive=f"{good_job['title']} {good_job['description'] or ''}".strip(),
                        negative=f"{bad_job['title']} {bad_job['description'] or ''}".strip(),
                        user_id=user_id,
                        good_job_id=good_job["job_id"],
                        bad_job_id=bad_job["job_id"],
                    ))

                if len(triplets) >= max_per_user:
                    break

        return triplets

    async def _build_eval_pairs(
        self,
        conn: asyncpg.Connection,
        cutoff_date: datetime,
    ) -> list[EvalPair]:
        """Строит eval пары из feedback данных."""

        rows = await conn.fetch("""
            SELECT
                uf.user_id,
                uf.job_id,
                uf.feedback,
                u.profile_text AS user_text,
                j.title,
                j.description
            FROM user_feedback uf
            INNER JOIN users u ON u.id = uf.user_id
            INNER JOIN jobs j ON j.id = uf.job_id
            WHERE uf.created_at >= $1
              AND u.profile_text IS NOT NULL
              AND u.profile_text != ''
            ORDER BY uf.created_at DESC
        """, cutoff_date)

        return [
            EvalPair(
                user_id=r["user_id"],
                job_id=r["job_id"],
                feedback=r["feedback"],
                user_text=(r["user_text"] or "").strip(),
                job_text=f"{r['title']} {(r['description'] or '')}".strip(),
            )
            for r in rows
        ]


def save_training_data(
    triplets: list[TrainingTriplet],
    eval_pairs: list[EvalPair],
    output_dir: str,
) -> dict[str, Any]:
    """
    Сохраняет training данные в JSON файлы.

    Returns:
        Dict со статистикой
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    date_str = datetime.utcnow().strftime("%Y%m%d")

    # Сохраняем триплеты
    triplets_file = output_path / f"triplets_{date_str}.json"
    triplets_data = [asdict(t) for t in triplets]
    triplets_file.write_text(json.dumps(triplets_data, ensure_ascii=False, indent=2))

    # Сохраняем eval пары
    eval_file = output_path / f"eval_pairs_{date_str}.json"
    eval_data = [asdict(p) for p in eval_pairs]
    eval_file.write_text(json.dumps(eval_data, ensure_ascii=False, indent=2))

    # Статистика
    good_count = sum(1 for p in eval_pairs if p.feedback == "good")
    bad_count = sum(1 for p in eval_pairs if p.feedback == "bad")

    stats = {
        "triplets_count": len(triplets),
        "eval_pairs_count": len(eval_pairs),
        "good_feedback": good_count,
        "bad_feedback": bad_count,
        "good_ratio": good_count / max(len(eval_pairs), 1),
        "date": date_str,
        "triplets_file": str(triplets_file),
        "eval_file": str(eval_file),
    }

    return stats
