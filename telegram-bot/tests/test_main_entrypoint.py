from __future__ import annotations

import pytest


def test_main_exits_on_invalid_env(bot, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "short")
    monkeypatch.setenv("API_AUTH_TOKEN", "short")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "short")
    with pytest.raises(SystemExit):
        bot.main()


def test_main_starts_polling_when_env_valid(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "https://api.example.com")

    called = {}

    def _run_polling(token, api_url, api_auth_token, api_user_hmac_secret):
        called["args"] = (token, api_url, api_auth_token, api_user_hmac_secret)

    monkeypatch.setattr(bot, "run_polling", _run_polling)
    bot.main()

    assert called["args"][0] == "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    assert called["args"][1] == "https://api.example.com"


def test_main_rejects_http_api_url_in_production(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "http://api.example.com")

    with pytest.raises(SystemExit):
        bot.main()
