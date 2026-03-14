"""PostgreSQL adapter."""

from ai_service.adapter.postgres.feedback_repository import PostgresFeedbackRepository
from ai_service.adapter.postgres.filter_event_repository import PostgresFilterEventRepository
from ai_service.adapter.postgres.match_repository import PostgresMatchRepository
from ai_service.adapter.postgres.pending_jobs_repository import PostgresPendingJobsRepository
from ai_service.adapter.postgres.repository import PostgresJobRepository
from ai_service.adapter.postgres.user_repository import PostgresUserRepository

__all__ = [
    "PostgresFeedbackRepository",
    "PostgresFilterEventRepository",
    "PostgresJobRepository",
    "PostgresUserRepository",
    "PostgresMatchRepository",
    "PostgresPendingJobsRepository",
]
