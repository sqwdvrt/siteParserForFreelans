# Откуда взять переменные окружения

| Переменная | Где взять |
|------------|-----------|
| **APP_ENV** | Режим запуска: `development` (локально) или `production` (включает строгие transport-policy проверки) |
| **TELEGRAM_BOT_TOKEN** | [@BotFather](https://t.me/BotFather) → /newbot → скопировать токен |
| **LOCAL_TELEGRAM_BOT_TOKEN** | Локальный dev-only токен для polling / smoke, если production token уже занят webhook'ом |
| **ALERTMANAGER_TELEGRAM_BOT_TOKEN / ALERTMANAGER_TELEGRAM_CHAT_ID** | Опциональные отдельные секреты Alertmanager (если не заданы, используются `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ID`) |
| **ALERTMANAGER_SLACK_WEBHOOK_URL** | Slack webhook для алертов Alertmanager (опционально) |
| **LOG_LEVEL** | Уровень логов telegram-bot (`DEBUG`, `INFO`, `WARNING`, `ERROR`), по умолчанию `INFO` |
| **DATABASE_URL** | PostgreSQL URL. Локально: `...?sslmode=disable`. В production: только `sslmode=require|verify-ca|verify-full` |
| **DATABASE_MIGRATE_URL** | Отдельный PostgreSQL URL только для миграций. Нужен в production, если `DATABASE_URL` указывает на transaction-pooler, а миграции требуют session/advisory locks |
| **POSTGRES_PASSWORD** | Сильный пароль PostgreSQL (обязателен; в `.env.example` только шаблон) |
| **REDIS_URL** | Redis URL. Локально: `redis://localhost:6379/0`. В production: только `rediss://...` + пароль |
| **API_URL** | URL Backend API. Локально: `http://localhost:8080`. В production (telegram-bot): только `https://` |
| **OLLAMA_URL** | URL Ollama для AI classifier. По умолчанию `http://ollama:11434` (docker-compose service `ollama`) |
| **OLLAMA_MODEL** | Модель Ollama для классификации. По умолчанию `llama3.2:3b-instruct-q4_K_M` |
| **OLLAMA_TIMEOUT_SEC** | Таймаут запроса к Ollama в секундах. По умолчанию `30` |
| **API_ADDR** | Порт, на котором слушает API. По умолчанию: `:8080` |
| **API_TLS_CERT_FILE / API_TLS_KEY_FILE** | TLS-сертификат и ключ API. Обязательны для backend API в `APP_ENV=production` |
| **API_AUTH_TOKEN** | Секрет для Bearer-auth backend API (должен совпадать у backend и telegram-bot) |
| **API_USER_HMAC_SECRET** | Отдельный секрет HMAC-подписи user-запросов (`POST /users`, `PUT /users/:id/profile`), должен совпадать у backend и telegram-bot |
| **API_NONCE_TTL_SEC** | TTL для одноразового `X-Request-Nonce` (anti-replay), по умолчанию `600` секунд |
| **API_RATE_LIMIT_WINDOW_SEC** | Окно rate-limit backend API, по умолчанию `60` секунд |
| **API_RATE_LIMIT_IP_RPM** | Лимит запросов API на IP в окне (`window`), по умолчанию `120` |
| **API_RATE_LIMIT_TG_RPM** | Лимит запросов API на Telegram ID в окне (`window`), по умолчанию `60` |
| **NOTIFY_PRO_MAX_PER_DAY** | Предпочтительный суточный лимит уведомлений на пользователя в `backend-notifier` (по умолчанию `5`) |
| **NOTIFY_MAX_PER_DAY** | Legacy fallback для суточного лимита уведомлений (используется, если `NOTIFY_PRO_MAX_PER_DAY` не задан) |
| **NOTIFIER_MAX_RETRIES** | Количество внутренних retry отправки в Telegram за один `Send` (по умолчанию `3`) |
| **NOTIFIER_RETRY_BASE_WAIT** | Базовая задержка retry в формате duration (`1s`, `500ms`), по умолчанию `1s` |
| **NOTIFIER_BREAKER_FAILURE_THRESHOLD** | Порог неуспешных `Send` до открытия circuit breaker (по умолчанию `3`) |
| **NOTIFIER_BREAKER_OPEN_INTERVAL** | Базовый интервал open-состояния breaker в формате duration, по умолчанию `30s` |
| **NOTIFIER_BREAKER_OPEN_JITTER** | Положительный jitter open/half-open окна breaker (`0..1`), по умолчанию `0.2` |
| **API_ALLOW_REDIS_DEGRADED** | Явный opt-in для запуска API без Redis (`1=true`) только в development. По умолчанию `0`: если Redis недоступен, backend API завершится с ошибкой (fail-closed). В `APP_ENV=production` значение `1` запрещено |
| **API_TRUSTED_PROXY_CIDRS** | CIDR-allowlist доверенных reverse-proxy (через запятую). `X-Forwarded-For`/`X-Real-IP` используются только для запросов от этих proxy |
| **POSTGRES_BIND_IP / REDIS_BIND_IP / API_BIND_IP** | Интерфейс публикации портов Docker на хосте. По умолчанию `127.0.0.1` (локальный доступ) |
| **POSTGRES_PORT / REDIS_PORT / API_PORT** | Порты публикации на хосте (`POSTGRES_PORT` по умолчанию `55432` для локального dev/integration) |

**Важно:** `.env` в `.gitignore` — секреты не попадут в репозиторий.

## Профили Deployment

- `docker-compose.yml` — локальная разработка (dev).
- `docker-compose.prod.yml` — production-профиль (требует `.env.production`).

### Production запуск (Docker Compose)

```bash
cp .env.production.example .env.production
# заполнить secure значения в .env.production

docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Production compose не поднимает локальные PostgreSQL/Redis контейнеры — используй внешние TLS endpoints.

## Требования по версиям

- **Python:** `>=3.11` (по `ai-service/pyproject.toml`).
- **Go (security baseline):** использовать патч-версию `>=1.25.8`.
  Для локальных запусков можно явно задавать:
  `GO_TOOLCHAIN=go1.25.8 bash ./scripts/go_test_backend.sh ./...`
  Если host-сеть ограничена (proxy.golang.org/github.com недоступны), используй:
  `BACKEND_GO_TEST_MODE=docker bash ./scripts/go_test_backend.sh ./...`
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
cd backend && GOTOOLCHAIN=go1.25.8 go run ./cmd/api

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
