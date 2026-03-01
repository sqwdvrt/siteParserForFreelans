from __future__ import annotations

from unittest.mock import MagicMock


def test_user_id_cache_hit_and_expire(bot):
    bot._USER_ID_CACHE.clear()
    bot._cache_user_id(telegram_id=200, user_id=321, now_monotonic=10.0)

    assert bot._get_cached_user_id(telegram_id=200, now_monotonic=10.1) == 321
    assert bot._get_cached_user_id(
        telegram_id=200,
        now_monotonic=10.0 + bot.USER_ID_CACHE_TTL_SEC + 0.001,
    ) is None


def test_resolve_user_id_returns_cached_value_without_post(bot, monkeypatch):
    bot._USER_ID_CACHE.clear()
    bot._cache_user_id(telegram_id=200, user_id=123, now_monotonic=10.0)
    monkeypatch.setattr(bot.time, "monotonic", lambda: 10.1)
    post_users = MagicMock(return_value=999)
    monkeypatch.setattr(bot, "post_users", post_users)

    resolved = bot._resolve_user_id("https://api.example.com", 200, "tok", "hmac")

    assert resolved == 123
    post_users.assert_not_called()


def test_resolve_user_id_fetches_and_caches_on_miss(bot, monkeypatch):
    bot._USER_ID_CACHE.clear()
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=555))

    resolved = bot._resolve_user_id("https://api.example.com", 200, "tok", "hmac")

    assert resolved == 555
    assert bot._get_cached_user_id(200) == 555


def test_resolve_user_id_does_not_cache_when_post_fails(bot, monkeypatch):
    bot._USER_ID_CACHE.clear()
    monkeypatch.setattr(bot, "post_users", MagicMock(return_value=None))

    resolved = bot._resolve_user_id("https://api.example.com", 200, "tok", "hmac")

    assert resolved is None
    assert bot._get_cached_user_id(200) is None


def test_user_id_cache_evicts_oldest_entry_when_maxsize_reached(bot, monkeypatch):
    monkeypatch.setattr(bot, "_USER_ID_CACHE", bot._build_user_id_cache(maxsize=2, ttl_sec=bot.USER_ID_CACHE_TTL_SEC))

    bot._cache_user_id(telegram_id=1, user_id=101, now_monotonic=10.0)
    bot._cache_user_id(telegram_id=2, user_id=102, now_monotonic=10.1)
    bot._cache_user_id(telegram_id=3, user_id=103, now_monotonic=10.2)

    assert bot._get_cached_user_id(telegram_id=1, now_monotonic=10.3) is None
    assert bot._get_cached_user_id(telegram_id=2, now_monotonic=10.3) == 102
    assert bot._get_cached_user_id(telegram_id=3, now_monotonic=10.3) == 103
