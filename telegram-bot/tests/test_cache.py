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


def test_conversation_state_cache_set_get_clear(bot, monkeypatch):
    monkeypatch.setattr(
        bot,
        "_CONVERSATION_STATE_CACHE",
        bot._build_conversation_state_cache(maxsize=2, ttl_sec=bot.CONVERSATION_STATE_TTL_SEC),
    )

    bot._set_conversation_state(telegram_id=200, state="await_profile", now_monotonic=10.0)
    assert bot._get_conversation_state(telegram_id=200, now_monotonic=10.1) == "await_profile"

    bot._clear_conversation_state(200)
    assert bot._get_conversation_state(telegram_id=200, now_monotonic=10.2) is None


def test_claim_update_id_deduplicates_until_ttl_expires(bot, monkeypatch):
    monkeypatch.setattr(
        bot,
        "_PROCESSED_UPDATE_CACHE",
        bot._build_processed_update_cache(maxsize=10, ttl_sec=5),
    )

    assert bot._claim_update_id(1, now_monotonic=10.0) == bot._UPDATE_CLAIMED
    assert bot._claim_update_id(1, now_monotonic=10.1) == bot._UPDATE_INFLIGHT
    assert bot._claim_update_id(1, now_monotonic=15.1) == bot._UPDATE_CLAIMED


def test_get_cached_user_id_restores_from_durable_state_store_after_local_restart(bot, monkeypatch):
    class FakeStateStore:
        def __init__(self) -> None:
            self.user_ids = {200: 321}

        def get_user_id(self, telegram_id: int) -> int | None:
            return self.user_ids.get(telegram_id)

        def cache_user_id(self, telegram_id: int, user_id: int) -> None:
            self.user_ids[telegram_id] = user_id

    monkeypatch.setattr(bot, "_STATE_STORE", FakeStateStore())
    bot._USER_ID_CACHE.clear()

    assert bot._get_cached_user_id(200, now_monotonic=10.0) == 321
    assert bot._USER_ID_CACHE[200][0] == 321


def test_get_conversation_state_restores_from_durable_state_store_after_local_restart(bot, monkeypatch):
    class FakeStateStore:
        def __init__(self) -> None:
            self.states = {200: "await_profile"}

        def get_conversation_state(self, telegram_id: int) -> str | None:
            return self.states.get(telegram_id)

        def set_conversation_state(self, telegram_id: int, state: str) -> None:
            self.states[telegram_id] = state

    monkeypatch.setattr(bot, "_STATE_STORE", FakeStateStore())
    bot._CONVERSATION_STATE_CACHE.clear()

    assert bot._get_conversation_state(200, now_monotonic=10.0) == "await_profile"
    assert bot._CONVERSATION_STATE_CACHE[200][0] == "await_profile"


def test_claim_update_id_deduplicates_via_durable_state_store_after_local_restart(bot, monkeypatch):
    class FakeStateStore:
        def __init__(self) -> None:
            self.claimed: dict[int, str] = {}

        def claim_update_id(self, update_id: int) -> str:
            state = self.claimed.get(update_id)
            if state is not None:
                return state
            self.claimed[update_id] = bot._UPDATE_INFLIGHT
            return bot._UPDATE_CLAIMED

        def complete_update_id(self, update_id: int) -> None:
            self.claimed[update_id] = bot._UPDATE_DONE

        def forget_update_id(self, update_id: int) -> None:
            self.claimed.pop(update_id, None)

    monkeypatch.setattr(bot, "_STATE_STORE", FakeStateStore())
    bot._PROCESSED_UPDATE_CACHE.clear()

    assert bot._claim_update_id(1, now_monotonic=10.0) == bot._UPDATE_CLAIMED
    bot._PROCESSED_UPDATE_CACHE.clear()
    assert bot._claim_update_id(1, now_monotonic=10.1) == bot._UPDATE_INFLIGHT
