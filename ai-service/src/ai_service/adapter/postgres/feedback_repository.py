"""Feedback repository — читает сигналы обратной связи пользователей для AI matching."""
from __future__ import annotations

import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


class FeedbackRepository:
    """Читает user_feedback для вычисления per-user статистики."""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url

    @contextmanager
    def _conn(self):
        conn = psycopg2.connect(self._db_url)
        try:
            yield conn
        finally:
            conn.close()

    def get_bad_ratio(self, user_id: int, days: int = 30) -> float:
        """Возвращает долю оценок 'bad' за последние *days* дней для пользователя.

        Возвращает 0.0, если обратной связи нет (без корректировки).
        """
        sql = """
            SELECT
                COUNT(*) FILTER (WHERE feedback = 'bad')  AS bad_count,
                COUNT(*)                                   AS total
            FROM user_feedback
            WHERE user_id = %(user_id)s
              AND created_at >= NOW() - %(days)s * INTERVAL '1 day'
        """
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, {"user_id": user_id, "days": days})
                row = cur.fetchone()
        if not row or row["total"] == 0:
            return 0.0
        return row["bad_count"] / row["total"]
