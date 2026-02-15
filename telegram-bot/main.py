#!/usr/bin/env python3
"""Telegram bot: getUpdates, /start → POST /users, /profile → PUT /users/:id/profile. Тонкий клиент."""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
import urllib.error

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


def _http_post(url: str, data: dict) -> tuple[int, dict | None]:
    """POST JSON. Возвращает (status_code, json_body или None)."""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
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


def _http_put(url: str, data: dict) -> int:
    """PUT JSON. Возвращает status_code."""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _http_get(url: str, params: dict) -> dict | None:
    """GET с query params. Возвращает json или None."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{qs}" if qs else url
    try:
        with urllib.request.urlopen(full_url, timeout=35) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def post_users(api_url: str, telegram_id: int) -> int | None:
    """POST /users → user_id. None при ошибке."""
    url = f"{api_url.rstrip('/')}/users"
    status, data = _http_post(url, {"telegram_id": telegram_id})
    if status != 200 or data is None:
        err = "API не отвечает" if status == 0 else f"HTTP {status}"
        logger.warning("POST /users failed: %s (url=%s)", err, url)
        return None
    return data.get("user_id")


def put_user_profile(api_url: str, user_id: int, profile_text: str) -> bool:
    """PUT /users/:id/profile. True при успехе."""
    url = f"{api_url.rstrip('/')}/users/{user_id}/profile"
    status = _http_put(url, {"profile_text": profile_text})
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


def run_polling(token: str, api_url: str) -> None:
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
                user_id = post_users(api_url, telegram_id)
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
                user_id = post_users(api_url, telegram_id)
                if user_id is None:
                    send_message(token, chat_id, "Сначала отправьте /start")
                    continue
                if put_user_profile(api_url, user_id, rest):
                    send_message(token, chat_id, "Профиль обновлён.")
                else:
                    send_message(token, chat_id, "Ошибка обновления профиля.")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)
    api_url = os.getenv("API_URL", "http://localhost:8080")
    logger.info("bot started, API=%s", api_url)
    run_polling(token, api_url)


if __name__ == "__main__":
    main()
