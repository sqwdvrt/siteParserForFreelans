"""PostgreSQL implementation of UserRepository."""

from __future__ import annotations

import contextlib
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector

from ai_service.domain.user import User
from ai_service.port.user_repository import UserRepository


class PostgresUserRepository(UserRepository):
    """UserRepository через PostgreSQL."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextlib.contextmanager
    def _conn(self):
        conn = psycopg2.connect(self._dsn)
        register_vector(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, telegram_id: int) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (telegram_id)
                    VALUES (%s)
                    ON CONFLICT (telegram_id) DO UPDATE SET updated_at = NOW()
                    RETURNING id
                    """,
                    (telegram_id,),
                )
                (user_id,) = cur.fetchone()
                return user_id

    def get_by_id(self, user_id: int) -> User | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, telegram_id, profile_text, embedding FROM users WHERE id = %s",
                    (user_id,),
                )
                row: dict[str, Any] | None = cur.fetchone()
        if row is None:
            return None
        emb = row["embedding"]
        if emb is not None and hasattr(emb, "tolist"):
            emb = emb.tolist()
        return User(
            id=row["id"],
            telegram_id=row["telegram_id"],
            profile_text=row["profile_text"],
            embedding=emb,
        )

    def get_by_telegram_id(self, telegram_id: int) -> User | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, telegram_id, profile_text, embedding FROM users WHERE telegram_id = %s",
                    (telegram_id,),
                )
                row: dict[str, Any] | None = cur.fetchone()
        if row is None:
            return None
        emb = row["embedding"]
        if emb is not None and hasattr(emb, "tolist"):
            emb = emb.tolist()
        return User(
            id=row["id"],
            telegram_id=row["telegram_id"],
            profile_text=row["profile_text"],
            embedding=emb,
        )

    def update_profile(self, user_id: int, profile_text: str) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users SET profile_text = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (profile_text, user_id),
                )

    def save_embedding(self, user_id: int, embedding: list[float]) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users SET embedding = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (embedding, user_id),
                )
