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
    monkeypatch.setenv("BOT_METRICS_PORT", "0")

    called = {}

    def _run_polling(token, api_url, api_auth_token, api_user_hmac_secret):
        called["args"] = (token, api_url, api_auth_token, api_user_hmac_secret)

    monkeypatch.setattr(bot, "set_my_commands", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot, "_start_metrics_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "_build_state_store", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "get_webhook_info", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "run_polling", _run_polling)
    bot.main()

    assert called["args"][0] == "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    assert called["args"][1] == "https://api.example.com"


def test_main_prefers_local_bot_token_in_development(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("LOCAL_TELEGRAM_BOT_TOKEN", "L1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "https://api.example.com")
    monkeypatch.setenv("BOT_METRICS_PORT", "0")

    called = {}

    def _run_polling(token, api_url, api_auth_token, api_user_hmac_secret):
        called["args"] = (token, api_url, api_auth_token, api_user_hmac_secret)

    monkeypatch.setattr(bot, "set_my_commands", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot, "_start_metrics_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "_build_state_store", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "get_webhook_info", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "run_polling", _run_polling)
    bot.main()

    assert called["args"][0] == "L1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"


def test_main_enters_standby_when_webhook_is_active_for_polling_token(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "https://api.example.com")
    monkeypatch.setenv("BOT_METRICS_PORT", "0")
    monkeypatch.setenv("POLLING_ACTIVE_WEBHOOK_POLICY", "standby")

    called = {"standby": None}

    monkeypatch.setattr(bot, "set_my_commands", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot, "_start_metrics_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "_build_state_store", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "get_webhook_info",
        lambda *_args, **_kwargs: {"url": "https://spectacular-warmth-production.up.railway.app/webhook"},
    )
    monkeypatch.setattr(bot, "run_polling", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("run_polling must not be called")))
    monkeypatch.setattr(bot, "run_polling_standby", lambda reason, **_kwargs: called.__setitem__("standby", reason))
    bot.main()

    assert "active webhook detected" in (called["standby"] or "")


def test_main_starts_webhook_when_env_valid(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BOT_MODE", "webhook")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "https://api.example.com")
    monkeypatch.setenv("WEBHOOK_URL", "https://bot.example.com/webhook")
    monkeypatch.setenv("WEBHOOK_SECRET_TOKEN", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("PORT", "9090")
    monkeypatch.setenv("WEBHOOK_MAX_CONNECTIONS", "55")
    monkeypatch.setenv("REDIS_URL", "rediss://default:secret@redis.example.com:6380/0")
    monkeypatch.setenv("BOT_METRICS_PORT", "0")

    called = {}

    def _run_webhook(
        token,
        webhook_url,
        webhook_secret,
        api_url,
        api_auth_token,
        api_user_hmac_secret,
        *,
        port,
        max_connections,
        server_factory=bot.ThreadingHTTPServer,
    ):
        _ = server_factory
        called["args"] = (
            token,
            webhook_url,
            webhook_secret,
            api_url,
            api_auth_token,
            api_user_hmac_secret,
            port,
            max_connections,
        )

    monkeypatch.setattr(bot, "set_my_commands", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot, "_start_metrics_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "_build_state_store", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "run_webhook", _run_webhook)
    bot.main()

    assert called["args"] == (
        "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
        "https://bot.example.com/webhook",
        "0123456789abcdef0123456789abcdef",
        "https://api.example.com",
        "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2",
        "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4",
        9090,
        55,
    )


def test_main_rejects_invalid_bot_mode(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("BOT_MODE", "invalid")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "https://api.example.com")
    monkeypatch.setenv("BOT_METRICS_PORT", "0")

    with pytest.raises(SystemExit):
        bot.main()


def test_main_rejects_http_api_url_in_production(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "http://api.example.com")
    monkeypatch.setenv("BOT_METRICS_PORT", "0")

    with pytest.raises(SystemExit):
        bot.main()


def test_main_rejects_missing_api_url_in_production(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.setenv("BOT_METRICS_PORT", "0")

    with pytest.raises(SystemExit):
        bot.main()


def test_main_rejects_non_tls_redis_url_in_production(bot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6")
    monkeypatch.setenv("API_AUTH_TOKEN", "B7nP3xQ9mR2tV8yL4cD6kF1hJ5sW0zUaX2")
    monkeypatch.setenv("API_USER_HMAC_SECRET", "C9vN4mB7qT2yH8kL1pR5sD3fG6jW0xZaY4")
    monkeypatch.setenv("API_URL", "https://api.example.com")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BOT_METRICS_PORT", "0")

    with pytest.raises(SystemExit):
        bot.main()
