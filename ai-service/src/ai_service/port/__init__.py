"""Ports (abstract interfaces)."""

from ai_service.port.classifier import ClassificationResult, Classifier
from ai_service.port.embedding import EmbeddingService
from ai_service.port.queue import JobQueueConsumer
from ai_service.port.repository import JobRepository
from ai_service.port.user_repository import UserRepository

__all__ = [
    "ClassificationResult",
    "Classifier",
    "EmbeddingService",
    "JobQueueConsumer",
    "JobRepository",
    "UserRepository",
]
