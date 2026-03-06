"""Feedback-based score adjuster for match candidates.

Shared by ProcessJobUseCase and ProcessUserRematchUseCase so that the
same adjustment logic is applied in both matching directions.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Callable

from ai_service.port.feedback_repository import FeedbackSignal
from ai_service.port.match_repository import MatchCandidate

logger = logging.getLogger(__name__)

# Boost cap:    score × 1.15 at most  (+15% for users who mostly like jobs)
# Penalty cap:  score × 0.5  at most  (-50% for users who dislike everything)
_MAX_MULTIPLIER = 1.15
_MIN_MULTIPLIER = 0.50

# Dead-zone: signals within ±0.2 are treated as noise and ignored
_SIGNAL_THRESHOLD = 0.20


def apply_signal(score: float, signal: FeedbackSignal) -> float:
    """Apply *signal* to *score* and return the adjusted score.

    Rules:
      - No feedback (total == 0)           → score unchanged
      - net > +SIGNAL_THRESHOLD (likes)    → boost  ×(1 + net × 0.15), max ×1.15
      - net < -SIGNAL_THRESHOLD (dislikes) → reduce ×(1 + net × 0.50), min ×0.50
      - |net| ≤ SIGNAL_THRESHOLD           → score unchanged (noise)
    """
    if signal.total == 0:
        return score

    net = signal.net  # good_ratio - bad_ratio, range -1..+1

    if net > _SIGNAL_THRESHOLD:
        multiplier = 1.0 + net * 0.15
    elif net < -_SIGNAL_THRESHOLD:
        multiplier = 1.0 + net * 0.50
    else:
        return score

    multiplier = max(_MIN_MULTIPLIER, min(_MAX_MULTIPLIER, multiplier))
    return score * multiplier


def adjust_candidates(
    candidates: list[MatchCandidate],
    get_job_skills: Callable[[int], list[str]],
    feedback_repo,
    threshold: float,
) -> list[MatchCandidate]:
    """Adjust match scores based on per-user feedback and filter below threshold.

    Args:
        candidates:    list of MatchCandidate from pgvector search.
        get_job_skills: callable(job_id) → list[str] — returns skill tags for a job.
        feedback_repo: FeedbackRepository implementation (or None-safe duck type).
        threshold:     minimum adjusted score to keep a candidate.

    Returns a new list with updated match_score values; candidates whose
    adjusted score falls below *threshold* are dropped.
    """
    if not candidates:
        return candidates

    result: list[MatchCandidate] = []
    for c in candidates:
        try:
            skills = get_job_skills(c.job_id)
            signal: FeedbackSignal = feedback_repo.get_feedback_signal(c.user_id, skills)
            new_score = apply_signal(c.match_score, signal)

            if new_score >= threshold:
                adjusted = dataclasses.replace(c, match_score=new_score)
                result.append(adjusted)
                if new_score != c.match_score:
                    logger.debug(
                        "feedback adjustment: job_id=%s user_id=%s score %.3f→%.3f "
                        "(net=%.2f good=%.2f bad=%.2f total=%d)",
                        c.job_id,
                        c.user_id,
                        c.match_score,
                        new_score,
                        signal.net,
                        signal.good_ratio,
                        signal.bad_ratio,
                        signal.total,
                    )
            else:
                logger.debug(
                    "feedback filter: job_id=%s user_id=%s score %.3f→%.3f "
                    "below threshold %.2f (net=%.2f)",
                    c.job_id,
                    c.user_id,
                    c.match_score,
                    new_score,
                    threshold,
                    signal.net,
                )
        except Exception as exc:
            logger.warning(
                "feedback adjustment failed for user_id=%s job_id=%s, keeping original: %s",
                c.user_id,
                c.job_id,
                exc,
            )
            result.append(c)  # on error keep original candidate

    return result
