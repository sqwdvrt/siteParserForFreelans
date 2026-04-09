"""ProcessUserEmbedUseCase: get user → encode(profile_text) → save embedding."""

from __future__ import annotations

import logging

from ai_service.port.embedding import EmbeddingService
from ai_service.port.profile_parser import ProfileParser
from ai_service.port.user_rematch_queue import UserRematchQueue
from ai_service.port.user_repository import UserRepository
from ai_service.util.profile_embedding_text import build_embedding_text
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
        profile_parser: ProfileParser | None = None,
    ) -> None:
        self._user_repo = user_repo
        self._embedding = embedding_service
        self._user_rematch_queue = user_rematch_queue
        self._profile_parser = profile_parser

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
        structured: dict[str, object] | None = None
        if self._profile_parser is not None:
            try:
                structured = self._profile_parser.parse(profile_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("profile parse failed for user_id=%s: %s", user_id, exc)
                structured = None
            if structured is not None:
                stack = tuple(str(item).strip().lower() for item in structured.get("stack", []) if str(item).strip())
                specialization = str(structured.get("specialization") or "other").strip().lower() or "other"
                level = str(structured.get("level") or "не указан").strip().lower() or "не указан"
                min_budget_hint_raw = structured.get("min_budget_hint")
                min_budget_hint = float(min_budget_hint_raw) if min_budget_hint_raw is not None else None
                self._user_repo.upsert_structured_profile(
                    user_id,
                    stack=stack,
                    specialization=specialization,
                    level=level,
                )
                self._user_repo.backfill_preferences_from_profile_parse(
                    user_id,
                    include_keywords=stack,
                    min_budget_hint=min_budget_hint,
                )
                text = build_embedding_text(profile_text, structured).strip()
                logger.info(
                    "embedding built with query expansion, stack=%s, specialization=%s",
                    list(stack),
                    specialization,
                )
            else:
                text = build_structured_profile_text(profile_text).strip()
        else:
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
