"""
Contrastive Dataset — модуль для подготовки данных contrastive learning.

Генерирует триплеты (anchor, positive, negative) для TripletLoss обучения.
"""
import json
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from sentence_transformers import InputExample
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class ContrastiveTriplet:
    anchor: str  # user profile text
    positive: str  # good job text
    negative: str  # bad job text
    user_id: int
    good_job_id: int
    bad_job_id: int


class ContrastiveDataset:
    """Датасет для contrastive learning с поддержкой tripet-формата."""

    def __init__(self, triplets: list[ContrastiveTriplet]):
        self.triplets = triplets

    def to_sentence_transformers_format(self) -> list[InputExample]:
        """Конвертирует в формат SentenceTransformer InputExample."""
        return [
            InputExample(texts=[t.anchor, t.positive, t.negative])
            for t in self.triplets
        ]

    def train_test_split(
        self,
        test_size: float = 0.2,
        random_seed: int = 42,
    ) -> tuple["ContrastiveDataset", "ContrastiveDataset"]:
        """
        Разделяет на train/test по пользователям (не по триплетам,
        чтобы не было data leakage).
        """
        random.seed(random_seed)

        # Группируем по user_id
        user_triplets: dict[int, list[ContrastiveTriplet]] = {}
        for t in self.triplets:
            user_triplets.setdefault(t.user_id, []).append(t)

        user_ids = list(user_triplets.keys())
        n_test = max(1, int(len(user_ids) * test_size))
        test_users = set(random.sample(user_ids, n_test))
        train_users = set(user_ids) - test_users

        train_triplets = [t for t in self.triplets if t.user_id in train_users]
        test_triplets = [t for t in self.triplets if t.user_id in test_users]

        return ContrastiveDataset(train_triplets), ContrastiveDataset(test_triplets)

    def statistics(self) -> dict:
        """Статистика датасета."""
        if not self.triplets:
            return {"count": 0}

        anchor_lens = [len(t.anchor) for t in self.triplets]
        positive_lens = [len(t.positive) for t in self.triplets]
        negative_lens = [len(t.negative) for t in self.triplets]

        unique_users = len(set(t.user_id for t in self.triplets))
        unique_good_jobs = len(set(t.good_job_id for t in self.triplets))
        unique_bad_jobs = len(set(t.bad_job_id for t in self.triplets))

        return {
            "count": len(self.triplets),
            "unique_users": unique_users,
            "unique_good_jobs": unique_good_jobs,
            "unique_bad_jobs": unique_bad_jobs,
            "anchor_len_mean": np.mean(anchor_lens),
            "anchor_len_median": np.median(anchor_lens),
            "positive_len_mean": np.mean(positive_lens),
            "positive_len_median": np.median(positive_lens),
            "negative_len_mean": np.mean(negative_lens),
            "negative_len_median": np.median(negative_lens),
        }

    def filter_by_text_length(
        self,
        min_len: int = 10,
        max_len: int = 5000,
    ) -> "ContrastiveDataset":
        """Фильтрует триплеты по длине текста."""
        filtered = [
            t
            for t in self.triplets
            if (
                min_len <= len(t.anchor) <= max_len
                and min_len <= len(t.positive) <= max_len
                and min_len <= len(t.negative) <= max_len
            )
        ]
        return ContrastiveDataset(filtered)

    def deduplicate(self) -> "ContrastiveDataset":
        """Удаляет дубликаты триплетов."""
        seen = set()
        unique = []
        for t in self.triplets:
            key = (t.user_id, t.good_job_id, t.bad_job_id)
            if key not in seen:
                seen.add(key)
                unique.append(t)
        return ContrastiveDataset(unique)

    def save_to_json(self, path: str) -> None:
        """Сохраняет датасет в JSON."""
        data = [asdict(t) for t in self.triplets]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))

    @classmethod
    def load_from_json(cls, path: str) -> "ContrastiveDataset":
        """Загружает датасет из JSON."""
        data = json.loads(Path(path).read_text())
        triplets = [ContrastiveTriplet(**item) for item in data]
        return cls(triplets)


class TripletMiningStrategy:
    """Абстрактная стратегия mining триплетов."""

    def mine(
        self,
        user_id: int,
        anchor_text: str,
        positive_jobs: list[tuple[int, str]],
        candidate_negatives: list[tuple[int, str]],
        n_samples: int = 5,
    ) -> list[ContrastiveTriplet]:
        raise NotImplementedError


class RandomNegativeMining(TripletMiningStrategy):
    """Случайная выборка негативов."""

    def mine(
        self,
        user_id: int,
        anchor_text: str,
        positive_jobs: list[tuple[int, str]],
        candidate_negatives: list[tuple[int, str]],
        n_samples: int = 5,
    ) -> list[ContrastiveTriplet]:
        triplets = []
        for pos_id, pos_text in positive_jobs:
            negatives = random.sample(
                candidate_negatives,
                min(n_samples, len(candidate_negatives)),
            )
            for neg_id, neg_text in negatives:
                triplets.append(ContrastiveTriplet(
                    anchor=anchor_text,
                    positive=pos_text,
                    negative=neg_text,
                    user_id=user_id,
                    good_job_id=pos_id,
                    bad_job_id=neg_id,
                ))
        return triplets


class HardNegativeMining(TripletMiningStrategy):
    """
    Hard negative mining: выбирает jobs с ВЫСОКОЙ cosine similarity,
    но BAD feedback (самые сложные для различения).
    """

    def __init__(
        self,
        embedding_model,
        user_embeddings: dict[int, np.ndarray],
        job_embeddings: dict[int, np.ndarray],
    ):
        self._model = embedding_model
        self._user_embeddings = user_embeddings
        self._job_embeddings = job_embeddings

    def mine(
        self,
        user_id: int,
        anchor_text: str,
        positive_jobs: list[tuple[int, str]],
        candidate_negatives: list[tuple[int, str]],
        n_samples: int = 5,
    ) -> list[ContrastiveTriplet]:
        user_emb = self._user_embeddings.get(user_id)
        if user_emb is None:
            # Fallback на случай если embedding не найден
            return RandomNegativeMining().mine(
                user_id, anchor_text, positive_jobs, candidate_negatives, n_samples
            )

        # Считаем similarity для кандидат-негативов
        neg_embeddings = []
        for neg_id, neg_text in candidate_negatives:
            neg_emb = self._job_embeddings.get(neg_id)
            if neg_emb is None:
                neg_emb = self._model.encode(neg_text)
            neg_embeddings.append((neg_id, neg_text, neg_emb))

        # Сортируем по similarity к anchor (hard negatives = high similarity)
        neg_embeddings.sort(
            key=lambda x: float(cosine_similarity([user_emb], [x[2]])[0, 0]),
            reverse=True,
        )

        # Берём top-n как hard negatives
        triplets = []
        for pos_id, pos_text in positive_jobs:
            for neg_id, neg_text, _ in neg_embeddings[:n_samples]:
                triplets.append(ContrastiveTriplet(
                    anchor=anchor_text,
                    positive=pos_text,
                    negative=neg_text,
                    user_id=user_id,
                    good_job_id=pos_id,
                    bad_job_id=neg_id,
                ))

        return triplets


class SemiHardNegativeMining(TripletMiningStrategy):
    """
    Semi-hard negative mining: выбирает negatives которые ближе к anchor
    чем positives, но не слишком близко (баланс сложности).
    """

    def __init__(
        self,
        embedding_model,
        user_embeddings: dict[int, np.ndarray],
        job_embeddings: dict[int, np.ndarray],
    ):
        self._model = embedding_model
        self._user_embeddings = user_embeddings
        self._job_embeddings = job_embeddings

    def mine(
        self,
        user_id: int,
        anchor_text: str,
        positive_jobs: list[tuple[int, str]],
        candidate_negatives: list[tuple[int, str]],
        n_samples: int = 5,
    ) -> list[ContrastiveTriplet]:
        user_emb = self._user_embeddings.get(user_id)
        if user_emb is None:
            return RandomNegativeMining().mine(
                user_id, anchor_text, positive_jobs, candidate_negatives, n_samples
            )

        # Вычисляем similarity к positive
        pos_similarities = []
        for pos_id, pos_text in positive_jobs:
            pos_emb = self._job_embeddings.get(pos_id)
            if pos_emb is None:
                pos_emb = self._model.encode(pos_text)
            sim = float(cosine_similarity([user_emb], [pos_emb])[0, 0])
            pos_similarities.append(sim)

        if not pos_similarities:
            return []

        avg_pos_sim = np.mean(pos_similarities)

        # Semi-hard negatives: similarity в диапазоне [avg_pos_sim - 0.2, avg_pos_sim + 0.1]
        neg_with_sim = []
        for neg_id, neg_text in candidate_negatives:
            neg_emb = self._job_embeddings.get(neg_id)
            if neg_emb is None:
                neg_emb = self._model.encode(neg_text)
            sim = float(cosine_similarity([user_emb], [neg_emb])[0, 0])
            # Semi-hard: negative ближе к anchor чем avg positive, но не слишком
            if sim < avg_pos_sim:
                neg_with_sim.append((neg_id, neg_text, sim))

        # Сортируем по similarity (ближе к positive = harder)
        neg_with_sim.sort(key=lambda x: x[2], reverse=True)

        triplets = []
        for pos_id, pos_text in positive_jobs:
            for neg_id, neg_text, _ in neg_with_sim[:n_samples]:
                triplets.append(ContrastiveTriplet(
                    anchor=anchor_text,
                    positive=pos_text,
                    negative=neg_text,
                    user_id=user_id,
                    good_job_id=pos_id,
                    bad_job_id=neg_id,
                ))

        return triplets
