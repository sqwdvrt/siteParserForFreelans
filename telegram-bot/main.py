#!/usr/bin/env python3
"""Telegram bot: getUpdates, /start → POST /users, /profile → PUT /users/:id/profile. Тонкий клиент."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import secrets
import hashlib
import hmac
import urllib.request
import urllib.error
import urllib.parse

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv("../.env")  # при запуске из telegram-bot/
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BASE = "https://api.telegram.org/bot"
FORBIDDEN_SECRET_PREFIXES = (
    "change_me",
    "changeme",
    "replace_me",
    "replace_with",
    "your_",
    "example_",
    "dummy_",
    "test_",
)


def _shannon_entropy_bits(secret: str) -> float:
    if not secret:
        return 0.0
    counts: dict[str, int] = {}
    for ch in secret:
        counts[ch] = counts.get(ch, 0) + 1
    total = float(len(secret))
    entropy_per_char = 0.0
    for count in counts.values():
        p = float(count) / total
        entropy_per_char += -p * math.log2(p)
    return entropy_per_char * total


def _validate_secret(name: str, secret: str, min_len: int) -> None:
    value = (secret or "").strip()
    if not value:
        raise ValueError(f"{name} not set")
    lower = value.lower()
    for prefix in FORBIDDEN_SECRET_PREFIXES:
        if lower.startswith(prefix):
            raise ValueError(f"{name} uses forbidden placeholder prefix '{prefix}'")
    if len(value) < min_len:
        raise ValueError(f"{name} must be at least {min_len} chars")
    min_entropy_bits = max(80.0, float(min_len) * 3.0)
    entropy = _shannon_entropy_bits(value)
    if entropy < min_entropy_bits:
        raise ValueError(f"{name} is too weak (entropy {entropy:.1f} < {min_entropy_bits:.1f})")


def _is_production_env(raw: str | None) -> bool:
    value = (raw or "").strip().lower()
    return value in {"prod", "production"}


def _validate_api_url_for_production(api_url: str) -> None:
    parsed = urllib.parse.urlsplit((api_url or "").strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("API_URL must use https:// in production")
    if not parsed.netloc:
        raise ValueError("API_URL must include host in production")


def _json_body(data: dict) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _http_post(url: str, data: dict, headers: dict[str, str] | None = None) -> tuple[int, dict | None]:
    """POST JSON. Возвращает (status_code, json_body или None)."""
    body = _json_body(data)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        if e.code >= 500 and err_body:
            logger.warning("API error %s: %s", e.code, err_body[:200])
        return e.code, None
    except Exception as e:
        logger.debug("HTTP error: %s", e)
        return 0, None


def _http_put(url: str, data: dict, headers: dict[str, str] | None = None) -> int:
    """PUT JSON. Возвращает status_code."""
    body = _json_body(data)
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _http_get(url: str, params: dict) -> dict | None:
    """GET с query params. Возвращает json или None."""
    qs = urllib.parse.urlencode(params)
    full_url = f"{url}?{qs}" if qs else url
    try:
        with urllib.request.urlopen(full_url, timeout=35) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.warning("HTTP GET failed: %s", e)
        return None


def _sign_user_request(
    user_hmac_secret: str,
    method: str,
    path: str,
    telegram_id: int,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    payload = "\n".join([method.upper(), path.strip(), str(telegram_id), timestamp.strip(), nonce.strip(), body_hash])
    return hmac.new(user_hmac_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _signed_user_headers(
    api_auth_token: str,
    user_hmac_secret: str,
    method: str,
    url: str,
    telegram_id: int,
    body: bytes,
) -> dict[str, str]:
    path = urllib.parse.urlsplit(url).path or "/"
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = _sign_user_request(user_hmac_secret, method, path, telegram_id, timestamp, nonce, body)
    return {
        "Authorization": f"Bearer {api_auth_token}",
        "X-Telegram-ID": str(telegram_id),
        "X-Request-Timestamp": timestamp,
        "X-Request-Nonce": nonce,
        "X-Request-Signature": signature,
    }


def post_users(api_url: str, telegram_id: int, api_auth_token: str, api_user_hmac_secret: str) -> int | None:
    """POST /users → user_id. None при ошибке."""
    url = f"{api_url.rstrip('/')}/users"
    payload = {"telegram_id": telegram_id}
    body = _json_body(payload)
    status, data = _http_post(
        url,
        payload,
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "POST", url, telegram_id, body),
    )
    if status != 200 or data is None:
        err = "API не отвечает" if status == 0 else f"HTTP {status}"
        logger.warning("POST /users failed: %s (url=%s)", err, url)
        return None
    return data.get("user_id")


def put_user_profile(
    api_url: str,
    user_id: int,
    telegram_id: int,
    profile_text: str,
    api_auth_token: str,
    api_user_hmac_secret: str,
) -> bool:
    """PUT /users/:id/profile. True при успехе."""
    url = f"{api_url.rstrip('/')}/users/{user_id}/profile"
    payload = {"profile_text": profile_text}
    body = _json_body(payload)
    status = _http_put(
        url,
        payload,
        headers=_signed_user_headers(api_auth_token, api_user_hmac_secret, "PUT", url, telegram_id, body),
    )
    if status != 204:
        logger.warning("PUT /users/:id/profile failed: status=%s", status)
        return False
    return True


def send_message(token: str, chat_id: int, text: str) -> bool:
    """Отправить сообщение в чат."""
    url = f"{TELEGRAM_BASE}{token}/sendMessage"
    status, _ = _http_post(url, {"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    if status != 200:
        logger.warning("sendMessage failed: status=%s", status)
        return False
    return True


def get_updates(token: str, offset: int | None, timeout: int = 30) -> tuple[list[dict], int | None]:
    """getUpdates. Возвращает (updates, next_offset)."""
    url = f"{TELEGRAM_BASE}{token}/getUpdates"
    params: dict = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    data = _http_get(url, params)
    if data is None or not data.get("ok"):
        return [], offset
    updates = data.get("result", [])
    next_offset = (max(u["update_id"] for u in updates) + 1) if updates else offset
    return updates, next_offset


def run_polling(token: str, api_url: str, api_auth_token: str, api_user_hmac_secret: str) -> None:
    """Цикл getUpdates → обработка /start, /profile."""
    offset: int | None = None
    while True:
        updates, offset = get_updates(token, offset)
        for u in updates:
            msg = u.get("message")
            if not msg:
                continue
            chat_id = msg.get("chat", {}).get("id")
            text = (msg.get("text") or "").strip()
            from_user = msg.get("from", {})
            telegram_id = from_user.get("id")
            if chat_id is None or telegram_id is None:
                continue

            if text == "/start":
                user_id = post_users(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
                if user_id is not None:
                    send_message(
                        token,
                        chat_id,
                        "Добро пожаловать! Отправьте /profile и текст профиля для настройки.",
                    )
                else:
                    send_message(token, chat_id, "Ошибка регистрации. Попробуйте позже.")

            elif text.startswith("/profile"):
                rest = text[len("/profile"):].strip()
                if not rest:
                    send_message(
                        token,
                        chat_id,
                        "Напишите: /profile <ваш текст профиля>",
                    )
                    continue
                user_id = post_users(api_url, telegram_id, api_auth_token, api_user_hmac_secret)
                if user_id is None:
                    send_message(token, chat_id, "Сначала отправьте /start")
                    continue
                if put_user_profile(api_url, user_id, telegram_id, rest, api_auth_token, api_user_hmac_secret):
                    send_message(token, chat_id, "Профиль обновлён.")
                else:
                    send_message(token, chat_id, "Ошибка обновления профиля.")


def main() -> None:
    app_env = os.getenv("APP_ENV", "development")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    api_auth_token = os.getenv("API_AUTH_TOKEN")
    api_user_hmac_secret = os.getenv("API_USER_HMAC_SECRET")
    api_url = os.getenv("API_URL", "http://localhost:8080")
    try:
        _validate_secret("TELEGRAM_BOT_TOKEN", token or "", 20)
        _validate_secret("API_AUTH_TOKEN", api_auth_token or "", 32)
        _validate_secret("API_USER_HMAC_SECRET", api_user_hmac_secret or "", 32)
        if _is_production_env(app_env):
            _validate_api_url_for_production(api_url)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)
    logger.info("bot started, API=%s", api_url)
    run_polling(token or "", api_url, api_auth_token or "", api_user_hmac_secret or "")


if __name__ == "__main__":
    main()
