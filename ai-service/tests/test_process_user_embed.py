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
        id=1,
        telegram_id=123,
        profile_text=(
            "Специализация: Backend-разработка. "
            "Уровень опыта: Senior (5+ лет). "
            "Навыки: Python, PostgreSQL."
        ),
        embedding=None,
    )
    embedding = MagicMock()
    embedding.encode.return_value = [0.1] * 384
    rematch_queue = MagicMock()
    uc = ProcessUserEmbedUseCase(user_repo, embedding, rematch_queue)
    assert uc.execute(1) is True
    embedding.encode.assert_called_once()
    encoded_text = embedding.encode.call_args.args[0]
    assert "Тип работы: web" in encoded_text
    assert "Опыт (лет): 6.0" in encoded_text
    assert "Стек: python, postgresql" in encoded_text
    user_repo.save_embedding.assert_called_once_with(1, [0.1] * 384)
    rematch_queue.enqueue.assert_called_once_with(1)


def test_execute_enriches_profile_and_backfills_preferences() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(
        id=7,
        telegram_id=321,
        profile_text="Senior Python backend developer with FastAPI, Django and PostgreSQL. Budget from 120000.",
        embedding=None,
    )
    embedding = MagicMock()
    embedding.encode.return_value = [0.2] * 384
    rematch_queue = MagicMock()
    parser = MagicMock()
    parser.parse.return_value = {
        "stack": ["fastapi", "django", "postgresql"],
        "specialization": "backend",
        "level": "senior",
        "preferred_work_type": "remote",
        "min_budget_hint": 120000,
    }

    uc = ProcessUserEmbedUseCase(
        user_repo,
        embedding,
        rematch_queue,
        profile_parser=parser,
    )

    assert uc.execute(7) is True

    parser.parse.assert_called_once()
    user_repo.upsert_structured_profile.assert_called_once_with(
        7,
        stack=("fastapi", "django", "postgresql"),
        specialization="backend",
        level="senior",
    )
    user_repo.backfill_preferences_from_profile_parse.assert_called_once_with(
        7,
        include_keywords=("fastapi", "django", "postgresql"),
        min_budget_hint=120000.0,
    )
    encoded_text = embedding.encode.call_args.args[0]
    assert "Технологии и стек: fastapi, django, postgresql" in encoded_text
    assert "Специализация: backend" in encoded_text
    assert "Уровень: senior" in encoded_text
    assert "Senior Python backend developer" in encoded_text


def test_execute_continues_with_plain_embedding_when_profile_parse_fails() -> None:
    user_repo = MagicMock()
    user_repo.get_by_id.return_value = User(
        id=9,
        telegram_id=999,
        profile_text="Python developer with Django",
        embedding=None,
    )
    embedding = MagicMock()
    embedding.encode.return_value = [0.3] * 384
    rematch_queue = MagicMock()
    parser = MagicMock()
    parser.parse.side_effect = TimeoutError("gemini timeout")

    uc = ProcessUserEmbedUseCase(
        user_repo,
        embedding,
        rematch_queue,
        profile_parser=parser,
    )

    assert uc.execute(9) is True

    user_repo.upsert_structured_profile.assert_not_called()
    user_repo.backfill_preferences_from_profile_parse.assert_not_called()
    encoded_text = embedding.encode.call_args.args[0]
    assert "Python developer with Django" in encoded_text
    assert "Технологии и стек:" not in encoded_text
