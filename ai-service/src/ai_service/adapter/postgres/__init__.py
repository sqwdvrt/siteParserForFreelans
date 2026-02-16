"""PostgreSQL adapter."""

from ai_service.adapter.postgres.match_repository import PostgresMatchRepository
from ai_service.adapter.postgres.repository import PostgresJobRepository
from ai_service.adapter.postgres.user_repository import PostgresUserRepository

__all__ = ["PostgresJobRepository", "PostgresUserRepository", "PostgresMatchRepository"]
