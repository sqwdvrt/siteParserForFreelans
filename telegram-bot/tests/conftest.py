from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

BOT_MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def _load_bot_module():
    spec = importlib.util.spec_from_file_location("telegram_bot_main", BOT_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load bot module from {BOT_MAIN_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bot():
    return _load_bot_module()
