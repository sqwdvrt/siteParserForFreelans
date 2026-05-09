"""Port: FeedbackRepository — reads user feedback signals for AI score adjustment."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class FeedbackSignal:
    """Feedback signal for a specific user (optionally filtered by job skills).

    Attributes:
        good_ratio: fraction of 'good' feedback (0.0 if no data).
        bad_ratio:  fraction of 'bad'  feedback (0.0 if no data).
        total:      number of feedback records used to compute the ratios.
    """

    good_ratio: float = field(default=0.0)
    bad_ratio: float = field(default=0.0)
    total: int = field(default=0)

    @property
    def net(self) -> float:
        """Net sentiment: good_ratio - bad_ratio (range -1 .. +1).

        Positive → user tends to like; negative → user tends to dislike.
        Returns 0.0 when total == 0 (no data).
        """
        return self.good_ratio - self.bad_ratio


class FeedbackRepository(ABC):
    """Abstract feedback repository used by AI matching to adjust match scores."""

    @abstractmethod
    def get_feedback_signal(
        self,
        user_id: int,
        job_skills: list[str] | None = None,
        days: int = 30,
    ) -> FeedbackSignal:
        """Return the feedback signal for *user_id* over the last *days* days.

        If *job_skills* is provided and non-empty the implementation **should**
        blend the global signal with a skill-specific signal (feedback on jobs
        that share skills with the target job).  If no skill-specific data
        exists the global signal is returned unchanged.

        Returns a FeedbackSignal with all-zero fields when no feedback exists.
        """
        ...
