from __future__ import annotations

import json


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is None:
            self.ttls.pop(key, None)
        else:
            self.ttls[key] = int(ex)
        return True

    def get(self, key: str):
        return self.values.get(key)

    def delete(self, *keys: str):
        removed = 0
        for key in keys:
            removed += 1 if self.values.pop(key, None) is not None else 0
            self.ttls.pop(key, None)
            if key in self.lists:
                self.lists.pop(key, None)
                removed += 1
        return removed

    def ttl(self, key: str):
        if key not in self.values:
            return -2
        return self.ttls.get(key, -1)

    def rpush(self, key: str, *values: str):
        bucket = self.lists.setdefault(key, [])
        bucket.extend(values)
        return len(bucket)

    def lpush(self, key: str, *values: str):
        bucket = self.lists.setdefault(key, [])
        for value in values:
            bucket.insert(0, value)
        return len(bucket)

    def brpoplpush(self, source: str, destination: str, timeout: int = 0):
        _ = timeout
        source_bucket = self.lists.setdefault(source, [])
        if not source_bucket:
            return None
        value = source_bucket.pop()
        self.lists.setdefault(destination, []).insert(0, value)
        return value

    def lrem(self, key: str, count: int, value: str):
        bucket = self.lists.setdefault(key, [])
        removed = 0
        kept: list[str] = []
        for item in bucket:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            kept.append(item)
        self.lists[key] = kept
        return removed

    def lrange(self, key: str, start: int, end: int):
        bucket = self.lists.get(key, [])
        if end == -1:
            end = len(bucket) - 1
        return bucket[start : end + 1]


def _build_store(bot):
    return bot._RedisStateStore(
        FakeRedis(),
        prefix="telegram-bot-test",
        user_id_ttl_sec=60,
        conversation_state_ttl_sec=60,
        processed_update_ttl_sec=60,
        batch_session_ttl_sec=24 * 60 * 60,
    )


def test_batch_session_update_preserves_existing_key_ttl(bot):
    store = _build_store(bot)
    key = "telegram-bot-test:batch-session:session-1"
    store._client.set(
        key,
        json.dumps({"version": 1, "telegram_id": 101, "current_index": 0, "items": [{"job_id": 7}]}),
        ex=24 * 60 * 60,
    )
    store._client.ttls[key] = 23 * 60 * 60

    store.set_batch_session(
        "session-1",
        {"version": 1, "telegram_id": 101, "current_index": 1, "items": [{"job_id": 7}]},
    )

    assert store._client.ttl(key) == 23 * 60 * 60


def test_batch_session_without_existing_key_uses_day_scale_ttl(bot):
    store = _build_store(bot)

    store.set_batch_session(
        "session-2",
        {"version": 1, "telegram_id": 101, "current_index": 0, "items": [{"job_id": 8}]},
    )

    assert store._client.ttl("telegram-bot-test:batch-session:session-2") == 24 * 60 * 60


def test_enqueue_webhook_update_deduplicates_by_update_id(bot):
    store = _build_store(bot)
    payload = json.dumps({"update_id": 101, "message": {"text": "/start"}})

    assert store.enqueue_webhook_update(101, payload) == "enqueued"
    assert store.enqueue_webhook_update(101, payload) == "duplicate"


def test_claim_next_webhook_update_returns_enqueued_payload(bot):
    store = _build_store(bot)
    payload = json.dumps({"update_id": 101, "message": {"text": "/start"}})
    store.enqueue_webhook_update(101, payload)

    claimed = store.claim_next_webhook_update(timeout_sec=0)

    assert claimed == (101, payload)


def test_recover_webhook_processing_requeues_stuck_updates(bot):
    store = _build_store(bot)
    payload = json.dumps({"update_id": 101, "message": {"text": "/start"}})
    store.enqueue_webhook_update(101, payload)
    assert store.claim_next_webhook_update(timeout_sec=0) == (101, payload)

    recovered = store.recover_webhook_processing()
    claimed_again = store.claim_next_webhook_update(timeout_sec=0)

    assert recovered == 1
    assert claimed_again == (101, payload)


def test_process_one_webhook_inbox_update_marks_done(bot, monkeypatch):
    processed: list[dict] = []
    store = _build_store(bot)
    monkeypatch.setattr(bot, "_STATE_STORE", store)
    store.enqueue_webhook_update(101, json.dumps({"update_id": 101, "message": {"text": "/start"}}))

    def _handle(update, token, api_url, api_auth_token, api_user_hmac_secret):
        _ = token
        _ = api_url
        _ = api_auth_token
        _ = api_user_hmac_secret
        processed.append(update)
        return True

    monkeypatch.setattr(bot, "_handle_update", _handle)

    assert (
        bot._process_one_webhook_inbox_update(
            "bot-token",
            "https://api.example.com",
            "tok",
            "h" * 32,
            timeout_sec=0,
        )
        is True
    )
    assert processed[0]["update_id"] == 101
