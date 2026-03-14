"""ProcessUserEmbedUseCase: get user → encode(profile_text) → save embedding."""

from __future__ import annotations

import logging

from ai_service.port.embedding import EmbeddingService
from ai_service.port.user_rematch_queue import UserRematchQueue
from ai_service.port.user_repository import UserRepository
from ai_service.util.profile_structurer import build_structured_profile_text
from ai_service.util.trace_context import get_trace_id

logger = logging.getLogger(__name__)


class ProcessUserEmbedUseCase:
    """Обработка user-embed: получить пользователя, закодировать profile_text, сохранить embedding."""

    def __init__(
        self,
        user_repo: UserRepository,
        embedding_service: EmbeddingService,
        user_rematch_queue: UserRematchQueue | None = None,
    ) -> None:
        self._user_repo = user_repo
        self._embedding = embedding_service
        self._user_rematch_queue = user_rematch_queue

    def execute(self, user_id: int) -> bool:
        """Обработать user_id. Возвращает True если embedding сохранён, False если пропущен."""
        trace_id = get_trace_id()
        user = self._user_repo.get_by_id(user_id)
        if user is None:
            if trace_id:
                logger.warning("user not found: %s trace_id=%s", user_id, trace_id)
            else:
                logger.warning("user not found: %s", user_id)
            return False

        profile_text = user.profile_text or ""
        text = build_structured_profile_text(profile_text).strip()
        if not text:
            if trace_id:
                logger.debug("empty profile_text, skip: %s trace_id=%s", user_id, trace_id)
            else:
                logger.debug("empty profile_text, skip: %s", user_id)
            return False

        embedding = self._embedding.encode(text)
        self._user_repo.save_embedding(user_id, embedding)
        if self._user_rematch_queue is not None:
            self._user_rematch_queue.enqueue(user_id)
        if trace_id:
            logger.info("saved embedding for user_id=%s trace_id=%s", user_id, trace_id)
        else:
            logger.info("saved embedding for user_id=%s", user_id)
        return True
