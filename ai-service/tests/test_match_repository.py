"""Тесты PostgresMatchRepository. Требует DATABASE_URL."""

from __future__ import annotations

import os

import pytest

from ai_service.adapter.postgres import PostgresMatchRepository


@pytest.fixture
def repo() -> PostgresMatchRepository:
    if not os.getenv("DATABASE_URL"):
        pytest.fail("DATABASE_URL not set; integration tests require Postgres (docker compose up -d postgres)")
    return PostgresMatchRepository(os.environ["DATABASE_URL"])


@pytest.fixture
def job_id() -> int:
    """Тестовый job_id (не в notifications)."""
    return 999999


def test_find_users_for_job_empty(repo: PostgresMatchRepository, job_id: int) -> None:
    """При threshold > 1 — пустой список (match_score всегда 0–1)."""
    embedding = [0.1] * 384
    result = repo.find_users_for_job(embedding, job_id, threshold=1.1)
    assert result == []


def test_find_users_for_job_returns_candidates(
    repo: PostgresMatchRepository, job_id: int
) -> None:
    """При наличии пользователя с похожим embedding — возвращает кандидатов."""
    import psycopg2
    from pgvector.psycopg2 import register_vector

    dsn = os.environ["DATABASE_URL"]
    with psycopg2.connect(dsn) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (telegram_id, embedding)
                VALUES (999888, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET embedding = EXCLUDED.embedding
                RETURNING id
                """,
                ([0.1] * 384,),
            )
            conn.commit()
            (user_id,) = cur.fetchone()

    result = repo.find_users_for_job([0.1] * 384, job_id, threshold=0.5)
    assert len(result) >= 1
    cand = next(c for c in result if c.user_id == user_id)
    assert cand.job_id == job_id
    assert cand.match_score >= 0.99  # идентичный вектор
    assert 0 <= cand.match_score <= 1


def test_find_users_for_job_excludes_notified(
    repo: PostgresMatchRepository, job_id: int
) -> None:
    """Исключает пользователей, уже в notifications для job_id."""
    import psycopg2
    from pgvector.psycopg2 import register_vector

    dsn = os.environ["DATABASE_URL"]
    with psycopg2.connect(dsn) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (source, url, title, raw_html)
                VALUES ('kwork', %s, 'T', '<p>x</p>')
                ON CONFLICT (url) DO UPDATE SET raw_html = EXCLUDED.raw_html
                RETURNING id
                """,
                (f"https://kwork.ru/test-{job_id}",),
            )
            conn.commit()
            (jid,) = cur.fetchone()
            cur.execute(
                "INSERT INTO users (telegram_id, embedding) VALUES (777666, %s) ON CONFLICT (telegram_id) DO UPDATE SET embedding = EXCLUDED.embedding RETURNING id",
                ([0.2] * 384,),
            )
            conn.commit()
            (uid,) = cur.fetchone()
            cur.execute(
                "INSERT INTO notifications (user_id, job_id, match_score) VALUES (%s, %s, 0.8) ON CONFLICT (user_id, job_id) DO NOTHING",
                (uid, jid),
            )
            conn.commit()

    result = repo.find_users_for_job([0.2] * 384, jid, threshold=0.5)
    result_user_ids = [c.user_id for c in result]
    assert uid not in result_user_ids


def test_similarity_threshold_excludes_low_scores(
    repo: PostgresMatchRepository, job_id: int
) -> None:
    """Порог: пользователи с similarity < threshold не возвращаются."""
    import psycopg2
    from pgvector.psycopg2 import register_vector

    # Ортогональные векторы: [1,0,0,...] и [0,1,0,...] — cosine similarity = 0
    low_sim_embedding = [0.0] * 384
    low_sim_embedding[0] = 1.0
    query_embedding = [0.0] * 384
    query_embedding[1] = 1.0
    dsn = os.environ["DATABASE_URL"]
    with psycopg2.connect(dsn) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (telegram_id, embedding)
                VALUES (555444, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET embedding = EXCLUDED.embedding
                RETURNING id
                """,
                (low_sim_embedding,),
            )
            conn.commit()
            (uid,) = cur.fetchone()

    # Запрос ортогональный к user — similarity ≈ 0, threshold=0.5 исключит
    result = repo.find_users_for_job(query_embedding, job_id, threshold=0.5)
    result_user_ids = [c.user_id for c in result]
    assert uid not in result_user_ids


def test_limit_respected(repo: PostgresMatchRepository, job_id: int) -> None:
    """Лимит: возвращается не более limit кандидатов."""
    result = repo.find_users_for_job(
        [0.1] * 384, job_id, threshold=0.0, limit=5
    )
    assert len(result) <= 5


def test_ordering_by_score_desc(
    repo: PostgresMatchRepository, job_id: int
) -> None:
    """Результаты отсортированы по similarity DESC."""
    result = repo.find_users_for_job(
        [0.1] * 384, job_id, threshold=0.0, limit=100
    )
    scores = [c.match_score for c in result]
    assert scores == sorted(scores, reverse=True)
