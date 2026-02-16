# E2E: Запуск и проверка (9.7–9.9)

## Требования

- Docker, docker compose
- `.env` с `API_AUTH_TOKEN`, `API_USER_HMAC_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ID`
- `TELEGRAM_ID` — ваш chat_id (узнать через @userinfobot). Добавьте в `.env` — E2E и уведомления будут на ваш аккаунт.

## Автоматизация

**Полный стек с ботом:**
```bash
docker compose up -d
```
Запускает: postgres, redis, backend-api, backend-crawler, backend-notifier, ai-service, **telegram-bot**.

Напишите боту в Telegram `/start` → `/profile Ваш профиль` — всё автоматически.

**E2E с вашим ID (из .env):**
```bash
./scripts/e2e_test.sh
```
Скрипт берёт `TELEGRAM_ID` из `.env` — уведомления придут вам.

## Быстрый запуск E2E

```bash
./scripts/e2e_test.sh
```

Скрипт выполняет:
- **9.7:** `docker compose up`, POST /users → проверка User в БД
- **9.8:** PUT /profile, вставка job, push в ai-process → проверка job_embeddings, push в match-notify
- **9.9:** Notifier обрабатывает match-notify → проверка логов (реальное сообщение — при `TELEGRAM_ID` = ваш chat_id)

## Ручная проверка 9.9

1. Добавьте в `.env`: `TELEGRAM_ID=ваш_chat_id` (узнать: @userinfobot)
2. Запустите `./scripts/e2e_test.sh` — ID подставится из .env
3. Или: `TELEGRAM_ID=ваш_chat_id ./scripts/e2e_test.sh`
4. Проверьте Telegram — должно прийти уведомление

## Проверка без скрипта

```bash
# 1. Запуск
docker compose up -d
# Подождать ~30 сек

# 2. POST /users (имитация /start)
# В API включена HMAC-подпись user-level запросов, поэтому проще использовать ./scripts/e2e_test.sh.
# Для ручной проверки нужно добавить заголовки:
# X-Telegram-ID, X-Request-Timestamp, X-Request-Signature (см. scripts/e2e_test.sh -> sign_user_request)

# 3. Проверка в БД
docker compose exec postgres psql -U site_parser -d site_parser -c "SELECT * FROM users;"

# 4. PUT /profile
# user_id из ответа шага 2 + те же user-level заголовки подписи
```

## Логи

- Backend API: `docker compose logs backend-api`
- Crawler: `docker compose logs backend-crawler`
- AI Service: `docker compose logs ai-service`
- Notifier: `docker compose logs backend-notifier`
- Telegram bot: `docker compose logs telegram-bot`
