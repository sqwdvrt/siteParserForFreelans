from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

from psycopg2.errors import UndefinedTable

from ai_service.adapter.postgres.user_repository import PostgresUserRepository
from ai_service.domain.user import UserPreferences


def test_load_preferences_returns_empty_when_table_missing() -> None:
    repo = PostgresUserRepository("postgresql://user:pass@localhost:5432/db")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = UndefinedTable("relation does not exist")

    got = repo._load_preferences(conn, 1)

    assert got == UserPreferences()
    conn.rollback.assert_called_once()


def test_load_tag_affinity_returns_empty_when_table_missing() -> None:
    repo = PostgresUserRepository("postgresql://user:pass@localhost:5432/db")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = UndefinedTable("relation does not exist")

    got = repo._load_tag_affinity(conn, 1)

    assert got == {}
    conn.rollback.assert_called_once()


def test_get_embedding_returns_vector_list() -> None:
    repo = PostgresUserRepository("postgresql://user:pass@localhost:5432/db")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    vector = MagicMock()
    vector.tolist.return_value = [0.1, 0.2, 0.3]
    cur.fetchone.return_value = (vector,)

    @contextlib.contextmanager
    def fake_conn():
        yield conn

    repo._conn = fake_conn  # type: ignore[method-assign]

    got = repo.get_embedding(42)

    assert got == [0.1, 0.2, 0.3]
    cur.execute.assert_called_once_with("SELECT embedding FROM users WHERE id = %s", (42,))


def test_get_embedding_returns_none_when_user_missing() -> None:
    repo = PostgresUserRepository("postgresql://user:pass@localhost:5432/db")
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = None

    @contextlib.contextmanager
    def fake_conn():
        yield conn

    repo._conn = fake_conn  # type: ignore[method-assign]

    assert repo.get_embedding(42) is None
