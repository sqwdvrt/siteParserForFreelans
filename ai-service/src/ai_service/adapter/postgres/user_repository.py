"""PostgreSQL implementation of UserRepository."""

from __future__ import annotations

from typing import Any

from psycopg2.errors import UndefinedTable
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

    def list_matchable_users(self) -> list[User]:
        with self._conn() as conn:
            try:
                rows = self._fetch_matchable_users_with_preferences(conn)
            except UndefinedTable:
                conn.rollback()
                rows = self._fetch_matchable_users_without_preferences(conn)
        return [self._row_to_matchable_user(row) for row in rows]

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

    def get_embedding(self, user_id: int) -> list[float] | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT embedding FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
        if row is None:
            return None
        embedding = row[0]
        if embedding is not None and hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        return embedding

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

    def upsert_structured_profile(
        self,
        user_id: int,
        *,
        stack: tuple[str, ...],
        specialization: str,
        level: str,
    ) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_profile_structured (user_id, stack, specialization, level, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (user_id) DO UPDATE
                    SET stack = EXCLUDED.stack,
                        specialization = EXCLUDED.specialization,
                        level = EXCLUDED.level,
                        updated_at = NOW()
                    """,
                    (user_id, list(stack), specialization, level),
                )

    def backfill_preferences_from_profile_parse(
        self,
        user_id: int,
        *,
        include_keywords: tuple[str, ...],
        min_budget_hint: float | None,
    ) -> None:
        keywords = list(self._normalize_text_values(include_keywords))
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_preferences (user_id, include_keywords, min_budget)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE
                    SET include_keywords = CASE
                            WHEN (
                                user_preferences.include_keywords IS NULL
                                OR cardinality(user_preferences.include_keywords) = 0
                            )
                                 AND EXCLUDED.include_keywords IS NOT NULL
                                 AND cardinality(EXCLUDED.include_keywords) > 0
                            THEN EXCLUDED.include_keywords
                            ELSE user_preferences.include_keywords
                        END,
                        min_budget = CASE
                            WHEN user_preferences.min_budget IS NULL AND EXCLUDED.min_budget IS NOT NULL
                            THEN EXCLUDED.min_budget
                            ELSE user_preferences.min_budget
                        END
                    """,
                    (user_id, keywords, min_budget_hint),
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

    def _row_to_matchable_user(self, row: dict[str, Any]) -> User:
        return User(
            id=row["id"],
            telegram_id=row["telegram_id"],
            profile_text=row.get("profile_text"),
            embedding=None,
            preferences=UserPreferences(
                include_keywords=tuple(self._normalize_text_values(row.get("include_keywords"))),
                exclude_keywords=tuple(self._normalize_text_values(row.get("exclude_keywords"))),
                min_budget=float(row["min_budget"]) if row.get("min_budget") is not None else None,
                max_budget=float(row["max_budget"]) if row.get("max_budget") is not None else None,
                preferred_sources=tuple(self._normalize_text_values(row.get("preferred_sources"))),
            ),
        )

    @staticmethod
    def _fetch_matchable_users_with_preferences(conn: Any) -> list[dict[str, Any]]:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.telegram_id,
                    COALESCE(u.profile_text, '') AS profile_text,
                    COALESCE(up.include_keywords, '{}') AS include_keywords,
                    COALESCE(up.exclude_keywords, '{}') AS exclude_keywords,
                    up.min_budget,
                    up.max_budget,
                    COALESCE(up.preferred_sources, '{}') AS preferred_sources
                FROM users u
                LEFT JOIN user_preferences up ON up.user_id = u.id
                WHERE u.embedding IS NOT NULL
                """
            )
            return list(cur.fetchall())

    @staticmethod
    def _fetch_matchable_users_without_preferences(conn: Any) -> list[dict[str, Any]]:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.telegram_id,
                    COALESCE(u.profile_text, '') AS profile_text,
                    ARRAY[]::text[] AS include_keywords,
                    ARRAY[]::text[] AS exclude_keywords,
                    NULL::numeric AS min_budget,
                    NULL::numeric AS max_budget,
                    ARRAY[]::text[] AS preferred_sources
                FROM users u
                WHERE u.embedding IS NOT NULL
                """
            )
            return list(cur.fetchall())

    def _load_preferences(self, conn: Any, user_id: int) -> UserPreferences:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    """
                    SELECT include_keywords, exclude_keywords, min_budget, max_budget, preferred_sources
                    FROM user_preferences
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
            except UndefinedTable:
                conn.rollback()
                return UserPreferences()
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
            try:
                cur.execute(
                    """
                    SELECT tag, weight
                    FROM user_tag_affinity
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
            except UndefinedTable:
                conn.rollback()
                return {}
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
