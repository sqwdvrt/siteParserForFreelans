"""Rule-based Actor fallback implementation."""

from __future__ import annotations

from ai_service.domain.job import Job
from ai_service.domain.ranked_job import RankedJob
from ai_service.domain.user import User
from ai_service.port.actor import ActorAgent


class RuleBasedActorAgent(ActorAgent):
    """Fallback Actor: keeps candidate order and returns top-N."""

    def select(
        self,
        user: User,
        candidates: list[Job],
        max_jobs: int = 5,
        critique: str | None = None,
    ) -> list[RankedJob]:
        del user, critique
        if max_jobs <= 0:
            return []

        top = candidates[:max_jobs]
        ranked: list[RankedJob] = []
        for index, job in enumerate(top):
            confidence = max(0.0, 1.0 - (index * 0.1))
            ranked.append(
                RankedJob(
                    job_id=job.id,
                    title=job.title,
                    why_it_fits="Соответствует вашему профилю по схожести.",
                    rank=index + 1,
                    actor_confidence=confidence,
                    final_score=job.final_score or job.match_score,
                    ranker_version=job.ranker_version,
                    reason_codes=tuple(job.reason_codes or ()),
                )
            )
        return ranked
