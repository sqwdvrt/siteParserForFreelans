"""Тесты get_job, save_embedding (PostgresJobRepository). Требует DATABASE_URL."""

import os

import pytest

from ai_service.adapter.postgres import PostgresJobRepository

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set",
)


@pytest.fixture
def repo() -> PostgresJobRepository:
    return PostgresJobRepository(os.environ["DATABASE_URL"])


@pytest.fixture
def job_id(repo: PostgresJobRepository) -> int:
    """Вставить тестовый job, вернуть id."""
    import psycopg2

    dsn = repo._dsn
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (source, url, title, description, raw_html)
                VALUES ('kwork',
                        'https://kwork.ru/test-' || extract(epoch from now())::text || '-' || random()::text,
                        'Test Title', 'Test desc', '<p>HTML</p>')
                RETURNING id
                """
            )
            (jid,) = cur.fetchone()
            conn.commit()
            return jid


def test_get_returns_job(repo: PostgresJobRepository, job_id: int) -> None:
    job = repo.get(job_id)
    assert job is not None
    assert job.id == job_id
    assert job.title == "Test Title"
    assert job.description == "Test desc"
    assert "<p>HTML</p>" in job.raw_html


def test_get_returns_none_for_missing(repo: PostgresJobRepository) -> None:
    assert repo.get(999999999) is None


def test_has_embedding_false_before_save(
    repo: PostgresJobRepository, job_id: int
) -> None:
    assert repo.has_embedding(job_id) is False


def test_save_embedding_and_has_embedding(
    repo: PostgresJobRepository, job_id: int
) -> None:
    embedding = [0.1] * 384
    metadata = {"model": "test", "text_length": 100}
    repo.save_embedding(job_id, embedding, metadata)
    assert repo.has_embedding(job_id) is True
