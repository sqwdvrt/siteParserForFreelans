"""Pytest hooks: загрузка .env из корня проекта для интеграционных тестов."""

from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Загрузить .env до сбора тестов."""
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent.parent
        env_file = root / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=True)
    except ImportError:
        pass
