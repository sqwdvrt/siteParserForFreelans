"""Pytest hooks: загрузка .env из корня проекта для интеграционных тестов."""

import os
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Загрузить .env до сбора тестов."""
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent.parent
        env_file = root / ".env"
        if env_file.exists():
            # Не перетираем переменные окружения, переданные извне
            # (CI/локальные прогоны могут задавать корректный DATABASE_URL).
            load_dotenv(env_file, override=False)
    except ImportError:
        pass
    config.addinivalue_line(
        "markers",
        "integration: requires external services (e.g. Postgres/Redis)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if os.getenv("INTEGRATION_TESTS") == "1":
        return
    skip_integration = pytest.mark.skip(
        reason="integration tests disabled (set INTEGRATION_TESTS=1 to enable)",
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
