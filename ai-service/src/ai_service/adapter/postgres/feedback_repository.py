"""Feedback repository — reads user feedback signals for AI matching."""
from __future__ import annotations

import logging

import psycopg2.extras

from ai_service.adapter.postgres._pooled_repository import PooledPostgresRepository
from ai_service.port.feedback_repository import FeedbackRepository, FeedbackSignal

logger = logging.getLogger(__name__)

# SQL: count good/bad feedback for a user, optionally filtered to jobs
# that share at least one skill with the target job (array overlap).
_SQL_GLOBAL = """
    SELECT
        COUNT(*) FILTER (WHERE uf.feedback = 'good') AS good_count,
        COUNT(*) FILTER (WHERE uf.feedback = 'bad')  AS bad_count,
        COUNT(*)                                       AS total
    FROM user_feedback uf
    WHERE uf.user_id = %s
      AND uf.created_at >= NOW() - (%s * INTERVAL '1 day')
"""

_SQL_BY_SKILLS = """
    SELECT
        COUNT(*) FILTER (WHERE uf.feedback = 'good') AS good_count,
        COUNT(*) FILTER (WHERE uf.feedback = 'bad')  AS bad_count,
        COUNT(*)                                       AS total
    FROM user_feedback uf
    JOIN jobs j ON j.id = uf.job_id
    WHERE uf.user_id = %s
      AND uf.created_at >= NOW() - (%s * INTERVAL '1 day')
      AND j.skills && %s
"""


class PostgresFeedbackRepository(PooledPostgresRepository, FeedbackRepository):
    """Reads user_feedback to compute per-user feedback signals.

    Inherits thread-safe connection pooling from PooledPostgresRepository —
    reuses connections instead of opening a new one per call.
    """

    def get_feedback_signal(
        self,
        user_id: int,
        job_skills: list[str] | None = None,
        days: int = 30,
    ) -> FeedbackSignal:
        """Return blended feedback signal for *user_id* over the last *days* days.

        When *job_skills* is non-empty:
          - computes a global signal (all jobs in the window)
          - computes a skill-specific signal (only jobs sharing >=1 skill)
          - returns a 50/50 blend when skill-specific data exists,
            otherwise falls back to the global signal.

        Returns FeedbackSignal(0.0, 0.0, 0) when no feedback exists.
        """
        global_sig = self._query_signal(user_id, skills=None, days=days)

        if not job_skills:
            return global_sig

        skill_sig = self._query_signal(user_id, skills=job_skills, days=days)
        if skill_sig.total == 0:
            # No skill-specific data — fall back to global
            return global_sig

        # Blend 50/50
        return FeedbackSignal(
            good_ratio=0.5 * global_sig.good_ratio + 0.5 * skill_sig.good_ratio,
            bad_ratio=0.5 * global_sig.bad_ratio + 0.5 * skill_sig.bad_ratio,
            total=global_sig.total,
        )

    # ── private ────────────────────────────────────────────────────────────────

    def _query_signal(
        self,
        user_id: int,
        skills: list[str] | None,
        days: int,
    ) -> FeedbackSignal:
        if skills:
            sql = _SQL_BY_SKILLS
            params = (user_id, days, skills)
        else:
            sql = _SQL_GLOBAL
            params = (user_id, days)

        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()

        if not row or row["total"] == 0:
            return FeedbackSignal()

        total = row["total"]
        return FeedbackSignal(
            good_ratio=row["good_count"] / total,
            bad_ratio=row["bad_count"] / total,
            total=total,
        )
