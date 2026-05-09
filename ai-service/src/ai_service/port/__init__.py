"""Ports (abstract interfaces)."""

from ai_service.port.actor import ActorAgent
from ai_service.port.classifier import ClassificationResult, Classifier
from ai_service.port.critic import CriticAgent
from ai_service.port.embedding import EmbeddingService
from ai_service.port.match_repository import MatchCandidate, MatchRepository
from ai_service.port.pending_jobs_repository import PendingJobsRepository
from ai_service.port.queue import JobQueueConsumer
from ai_service.port.repository import JobRepository
from ai_service.port.user_rematch_queue import UserRematchQueue
from ai_service.port.user_repository import UserRepository

__all__ = [
    "ActorAgent",
    "ClassificationResult",
    "Classifier",
    "CriticAgent",
    "EmbeddingService",
    "MatchCandidate",
    "MatchRepository",
    "PendingJobsRepository",
    "JobQueueConsumer",
    "JobRepository",
    "UserRematchQueue",
    "UserRepository",
]
