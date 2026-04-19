# Откуда взять переменные окружения

| Переменная | Где взять |
|------------|-----------|
| **APP_ENV** | Режим запуска: `development` (локально) или `production` (включает строгие transport-policy проверки и требует TLS) |
| **TELEGRAM_BOT_TOKEN** | [@BotFather](https://t.me/BotFather) → /newbot → скопировать токен |
| **LOCAL_TELEGRAM_BOT_TOKEN** | Локальный dev-only токен для polling / smoke, если production token уже занят webhook'ом |
| **ALERTMANAGER_TELEGRAM_BOT_TOKEN / ALERTMANAGER_TELEGRAM_CHAT_ID** | Опциональные отдельные секреты Alertmanager (если не заданы, используются `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ID`) |
| **ALERTMANAGER_SLACK_WEBHOOK_URL** | Slack webhook для алертов Alertmanager (опционально) |
| **LOG_LEVEL** | Уровень логов telegram-bot (`DEBUG`, `INFO`, `WARNING`, `ERROR`), по умолчанию `INFO` |
| **DATABASE_URL** | PostgreSQL URL. Локально: `...?sslmode=disable`. В production: только `sslmode=require|verify-ca|verify-full` |
| **DATABASE_MIGRATE_URL** | Отдельный PostgreSQL URL только для миграций. Нужен в production, если `DATABASE_URL` указывает на transaction-pooler, а миграции требуют session/advisory locks |
| **PG_POOL_MAX_CONNS / PG_POOL_MIN_CONNS / PG_POOL_ACQUIRE_TIMEOUT** | Тюнинг postgres pool. В текущем проектном `.env` используются `10 / 1 / 15s` |
| **POSTGRES_PASSWORD** | Сильный пароль PostgreSQL (обязателен; в `.env.example` только шаблон) |
| **REDIS_URL** | Redis URL. Локально: `redis://localhost:6379/0`. В production: только `rediss://...` + пароль |
| **API_URL** | URL Backend API. Локально: `http://localhost:8080`. В production для `telegram-bot`: только `https://` |
| **BOT_MODE** | Режим `telegram-bot`. Локально допустим `polling`, в production по contract только `webhook` |
| **WEBHOOK_URL / WEBHOOK_SECRET_TOKEN** | Production webhook ingress для `telegram-bot`. Обязательны при `BOT_MODE=webhook`; на текущем VPS это `https://api.freematch.ru/webhook` и отдельный secret |
| **BROWSER_SERVICE_URL** | URL browser render service для crawler. В текущем проектном `.env` используется `http://browser-service:8090` |
| **LLM_PROVIDER** | Провайдер actor для `ai-ac-consumer`: только `gemini`. Переменная обязательна для этого процесса |
| **GEMINI_API_KEY** | Ключ Gemini API. Обязателен |
| **GEMINI_MODEL / GEMINI_ACTOR_MODEL** | Базовая и role-specific Gemini модель actor. По умолчанию `gemini-2.0-flash` |
| **ACTOR_GEMINI_TIMEOUT_SEC** | Таймаут actor-запроса к Gemini. По умолчанию `30` |
| **EMBEDDING_MODEL / RERANK_MODEL** | Модели embedding и rerank. По умолчанию `all-MiniLM-L6-v2` и `BAAI/bge-reranker-base` |
| **EMBEDDING_MODELS_DIR / RERANK_MODELS_DIR** | Каталоги с локально предзагруженными моделями. По умолчанию `/opt/models` |
| **EMBEDDING_REQUIRE_LOCAL / RERANK_REQUIRE_LOCAL** | В текущем проектном `.env` `EMBEDDING_REQUIRE_LOCAL=0`; для `RERANK_REQUIRE_LOCAL` unset-значение наследует `EMBEDDING_REQUIRE_LOCAL` |
| **AI_WARMUP_ENABLED** | В текущем проектном `.env` `0`, чтобы уменьшить peak RAM на старте embedding consumers |
| **SIMILARITY_THRESHOLD** | В текущем проектном `.env` tuned значение `0.35` для `ai-service`/`ai-user-rematch` |
| **AI_USER_REMATCH_HEALTH_PORT** | Внутренний health-port `ai-user-rematch`. По умолчанию `8092` |
| **PGADMIN_EMAIL / PGADMIN_PASSWORD** | Опциональные креды `pgAdmin` для `admin`-profile; сервис публикуется только на `127.0.0.1:5050` и открывается через SSH tunnel |
| **VPS_HEALTH_TELEGRAM_BOT_TOKEN / VPS_HEALTH_TELEGRAM_CHAT_ID** | Опциональный отдельный Telegram target для `scripts/vps_health_report.py`; если не заданы, используются Alertmanager vars, затем `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ID` |
| **BACKEND_API_METRICS_TARGET / BACKEND_API_METRICS_SCHEME / BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY** | Production scrape overrides для `docker-compose.monitoring.yml`; на VPS по умолчанию `backend-api:8443`, `https`, `true` |
| **REDIS_EXPORTER_REDIS_ADDR** | Опциональный override для `redis-exporter`; если не задан, используется `REDIS_URL`, затем `redis://redis:6379` |
| **POSTGRES_EXPORTER_DATA_SOURCE_NAME** | Обязательный DSN для `postgres-exporter` в `monitoring` profile; в production это TLS-enabled connection к self-hosted Postgres |
| **GRAFANA_POSTGRES_HOST / GRAFANA_POSTGRES_PORT / GRAFANA_POSTGRES_SSLMODE** | Параметры datasource Grafana для `monitoring` profile; на VPS по умолчанию `postgres:5432` и `require` |
| **GRAFANA_ADMIN_PASSWORD** | Обязательный admin password для Grafana в `monitoring` profile |
| **GRAFANA_POSTGRES_PASSWORD** | Опциональный override password для Grafana datasource; если не задан, берётся `POSTGRES_PASSWORD` |
| **AI_AC_BATCH_POP_TIMEOUT_SEC / AC_LEASE_TIMEOUT_SEC / AC_PENDING_RETENTION_DAYS / AC_PENDING_CLEANUP_INTERVAL_SEC / AC_PENDING_METRICS_REFRESH_SEC** | Настройки BRPOP/lease/cleanup/metrics для `pending_ac_jobs` в `ai-ac-consumer` |
| **API_ADDR** | Порт, на котором слушает API. По умолчанию: `:8080` |
| **API_TLS_CERT_HOST_PATH / API_TLS_KEY_HOST_PATH** | Host path до TLS-сертификата и ключа, которые `docker-compose.prod.yml` bind-mount'ит в `backend-api` на VPS |
| **API_TLS_CERT_FILE / API_TLS_KEY_FILE** | TLS-сертификат и ключ API. Обязательны для backend API в `APP_ENV=production` |
| **API_AUTH_TOKEN** | Секрет для Bearer-auth backend API (должен совпадать у backend и telegram-bot) |
| **API_USER_HMAC_SECRET** | Отдельный секрет HMAC-подписи user-запросов (`POST /users`, `PUT /users/:id/profile`), должен совпадать у backend и telegram-bot |
| **ADMIN_AUTH_TOKEN** | Отдельный Bearer token для admin API-эндпоинтов. В production обязателен по `production_env_contract` |
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
| **INFRA_CERTS_DIR** | Override path к internal CA-сертификату для `docker-compose.ssl.yml`. По умолчанию `/home/deploy/infra/certs` |

**Важно:** `.env` в `.gitignore` — секреты не попадут в репозиторий.

## Профили Deployment

- `docker-compose.yml` — локальная разработка (dev).
- `docker-compose.prod.yml` — production-профиль (требует `.env.production`).
  Канонический VPS запуск использует его вместе с `docker-compose.ssl.yml`.
  `docker-compose.monitoring.yml` подключается отдельным overlay, если нужен monitoring profile.

### Production запуск (Docker Compose)

```bash
cp .env.production.example .env.production
# заполнить secure значения в .env.production

docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml pull
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml up -d --no-build
```

Production `.env.production` должен содержать digest-pinned `BACKEND_IMAGE`, `BROWSER_SERVICE_IMAGE`, `TELEGRAM_BOT_IMAGE` и `AI_IMAGE`, а также host path secrets для TLS.

Production compose не поднимает локальные PostgreSQL/Redis контейнеры — используй внешние TLS endpoints.

### VPS automation

- `scripts/install_vps_health_report_timer.sh` ставит systemd timer при запуске от root и иначе пишет job в user crontab; интервал в обоих случаях `*/5 * * * *`.
- `scripts/vps_health_report.py generate` пишет JSON и append-only log в `/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/` и шлёт Telegram только при `WARN/FAIL`, если fingerprint проблемы изменился.
- `scripts/backup_vps_cron.sh` по умолчанию хранит backup-файлы в `/home/deploy/app/.siteParserForFreelans-deploy/shared/backups/` и удаляет `.dump`/`.sha256` старше 7 дней.

## Требования по версиям

- **Python:** `>=3.11` (по `ai-service/pyproject.toml`).
- **Go (security baseline):** использовать патч-версию `>=1.25.9`.
  Для локальных запусков можно явно задавать:
  `GO_TOOLCHAIN=go1.25.9 bash ./scripts/go_test_backend.sh ./...`
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
cd backend && GOTOOLCHAIN=go1.25.9 go run ./cmd/api

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
