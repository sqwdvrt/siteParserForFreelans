"""PostgreSQL implementation of UserRepository."""

from __future__ import annotations

from typing import Any

from psycopg2.extras import RealDictCursor

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.domain.user import User, UserPreferences
from ai_service.port.user_repository import UserRepository


class PostgresUserRepository(PooledPostgresRepository, UserRepository):
    """UserRepository через PostgreSQL."""

    def __init__(
        self,
        dsn: str,
        *,
        minconn: int = 1,
        maxconn: int = 10,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        super().__init__(dsn, minconn=minconn, maxconn=maxconn, statement_timeout_ms=statement_timeout_ms)

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
            return self._row_to_user(conn, row)

    def get_by_telegram_id(self, telegram_id: int) -> User | None:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, telegram_id, profile_text, embedding FROM users WHERE telegram_id = %s",
                    (telegram_id,),
                )
                row: dict[str, Any] | None = cur.fetchone()
            return self._row_to_user(conn, row)

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

    def _row_to_user(self, conn: Any, row: dict[str, Any] | None) -> User | None:
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
            preferences=self._load_preferences(conn, row["id"]),
            tag_affinity=self._load_tag_affinity(conn, row["id"]),
        )

    def _load_preferences(self, conn: Any, user_id: int) -> UserPreferences:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT include_keywords, exclude_keywords, min_budget, max_budget, preferred_sources
                FROM user_preferences
                WHERE user_id = %s
                """,
                (user_id,),
            )
            row: dict[str, Any] | None = cur.fetchone()
        if row is None:
            return UserPreferences()
        return UserPreferences(
            include_keywords=tuple(self._normalize_text_values(row.get("include_keywords"))),
            exclude_keywords=tuple(self._normalize_text_values(row.get("exclude_keywords"))),
            min_budget=float(row["min_budget"]) if row.get("min_budget") is not None else None,
            max_budget=float(row["max_budget"]) if row.get("max_budget") is not None else None,
            preferred_sources=tuple(self._normalize_text_values(row.get("preferred_sources"))),
        )

    def _load_tag_affinity(self, conn: Any, user_id: int) -> dict[str, float]:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT tag, weight
                FROM user_tag_affinity
                WHERE user_id = %s
                """,
                (user_id,),
            )
            rows: list[dict[str, Any]] = cur.fetchall()
        affinity: dict[str, float] = {}
        for row in rows:
            tag = str(row.get("tag") or "").strip().lower()
            if not tag:
                continue
            affinity[tag] = float(row.get("weight") or 0.0)
        return affinity

    @staticmethod
    def _normalize_text_values(values: Any) -> list[str]:
        if not values:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw or "").strip().lower()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized
