# Откуда взять переменные окружения

| Переменная | Где взять |
|------------|-----------|
| **APP_ENV** | Режим запуска: `development` (локально) или `production` (включает строгие transport-policy проверки) |
| **TELEGRAM_BOT_TOKEN** | [@BotFather](https://t.me/BotFather) → /newbot → скопировать токен |
| **DATABASE_URL** | PostgreSQL URL. Локально: `...?sslmode=disable`. В production: только `sslmode=require|verify-ca|verify-full` |
| **POSTGRES_PASSWORD** | Сильный пароль PostgreSQL (обязателен; в `.env.example` только шаблон) |
| **REDIS_URL** | Redis URL. Локально: `redis://localhost:6379/0`. В production: только `rediss://...` + пароль |
| **API_URL** | URL Backend API. Локально: `http://localhost:8080`. В production (telegram-bot): только `https://` |
| **API_ADDR** | Порт, на котором слушает API. По умолчанию: `:8080` |
| **API_TLS_CERT_FILE / API_TLS_KEY_FILE** | TLS-сертификат и ключ API. Обязательны для backend API в `APP_ENV=production` |
| **API_AUTH_TOKEN** | Секрет для Bearer-auth backend API (должен совпадать у backend и telegram-bot) |
| **API_USER_HMAC_SECRET** | Отдельный секрет HMAC-подписи user-запросов (`POST /users`, `PUT /users/:id/profile`), должен совпадать у backend и telegram-bot |
| **API_NONCE_TTL_SEC** | TTL для одноразового `X-Request-Nonce` (anti-replay), по умолчанию `600` секунд |
| **API_RATE_LIMIT_WINDOW_SEC** | Окно rate-limit backend API, по умолчанию `60` секунд |
| **API_RATE_LIMIT_IP_RPM** | Лимит запросов API на IP в окне (`window`), по умолчанию `120` |
| **API_RATE_LIMIT_TG_RPM** | Лимит запросов API на Telegram ID в окне (`window`), по умолчанию `60` |
| **POSTGRES_BIND_IP / REDIS_BIND_IP / API_BIND_IP** | Интерфейс публикации портов Docker на хосте. По умолчанию `127.0.0.1` (локальный доступ) |
| **POSTGRES_PORT / REDIS_PORT / API_PORT** | Порты публикации на хосте |

**Важно:** `.env` в `.gitignore` — секреты не попадут в репозиторий.

## Требования по версиям

- **Python:** `>=3.11` (по `ai-service/pyproject.toml`).
- **Go (security baseline):** использовать патч-версию `>=1.25.7`.
  Для локальных запусков можно явно задавать:
  `GOTOOLCHAIN=go1.25.7 go test ./...`
- Проверка Python:
  `python3.11 --version`

### Интеграционные тесты (ai-service)

Тесты `test_match_repository.py` и `test_postgres_repository.py` требуют PostgreSQL. Перед запуском:

```bash
# Поднять PostgreSQL (из корня проекта)
docker compose up -d postgres

# Запустить все тесты (conftest.py загружает .env из корня)
cd ai-service && python3.11 -m pytest tests/ -v
```

Без `DATABASE_URL` (и без `.env`) эти тесты пропускаются (skip). С `.env` и запущенным PostgreSQL — выполняются.

**Если ошибка `password authentication failed`:** проверь, что в `.env` (в корне проекта) указан правильный пароль в `DATABASE_URL`. Для docker-compose обычно `postgres://site_parser:site_parser@...`. Не используй плейсхолдер `your_password_here` из `.env.example`.

### Запуск (из корня проекта)

**Перед первым запуском AI Service:**
```bash
python3.11 -m pip install python-dotenv redis psycopg2-binary pgvector sentence-transformers "pillow>=12.1.1"
```

**Telegram-бот:** `python3.11 -m pip install python-dotenv`

```bash
# API
cd backend && GOTOOLCHAIN=go1.25.7 go run ./cmd/api

# AI user-embed consumer
cd ai-service && PYTHONPATH=src python3.11 cmd/user_embed_consumer/main.py

# Telegram-бот
cd telegram-bot && python3.11 main.py
```

### Security baseline check

```bash
./scripts/security_baseline_check.sh
# Или явно указать интерпретатор Python >=3.11:
PYTHON_BIN=python3.12 ./scripts/security_baseline_check.sh
```
