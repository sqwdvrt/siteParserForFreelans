"""Тесты ProcessUserEmbedUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

from ai_service.domain.user import User
from ai_service.usecase.process_user_embed import ProcessUserEmbedUseCase


def test_execute_returns_false_when_user_not_found() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = None
    embedding = MagicMock()
    rematch_queue = MagicMock()
    uc = ProcessUserEmbedUseCase(user_repo, embedding, rematch_queue)
    assert uc.execute(42) is False
    user_repo.get_by_id.assert_called_once_with(42)
    embedding.encode.assert_not_called()
    user_repo.save_embedding.assert_not_called()
    rematch_queue.enqueue.assert_not_called()


def test_execute_skips_when_profile_empty() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(
        id=1, telegram_id=123, profile_text="", embedding=None
    )
    embedding = MagicMock()
    rematch_queue = MagicMock()
    uc = ProcessUserEmbedUseCase(user_repo, embedding, rematch_queue)
    assert uc.execute(1) is False
    embedding.encode.assert_not_called()
    user_repo.save_embedding.assert_not_called()
    rematch_queue.enqueue.assert_not_called()


def test_execute_saves_embedding_and_enqueues_rematch() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(
        id=1, telegram_id=123, profile_text="I am a developer", embedding=None
    )
    embedding = MagicMock()
    embedding.encode.return_value = [0.1] * 384
    rematch_queue = MagicMock()
    uc = ProcessUserEmbedUseCase(user_repo, embedding, rematch_queue)
    assert uc.execute(1) is True
    embedding.encode.assert_called_once_with("I am a developer")
    user_repo.save_embedding.assert_called_once_with(1, [0.1] * 384)
    rematch_queue.enqueue.assert_called_once_with(1)
