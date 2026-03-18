# Site Parser for Freelance

Автоматическое обнаружение новых фриланс-проектов, их анализ и отправка пользователю только релевантных заказов.

**Ветки:** `develop` — основная разработка, `main` — стабильный релиз.

**Сервис только:** находит → анализирует → ранжирует → уведомляет.

**Это не:** биржа фриланса, маркетплейс, автоотклик, инструмент подачи заявок.

**KPI:** ≤ 5 уведомлений в день, большинство релевантны. Оптимизировать релевантность, а не количество источников.

**Не:** агрегатор вакансий, массовая рассылка, парсинг без задержек, обход логина, автоотклики.

**Matching:** embedding similarity (cosine), не keyword filtering.

**Архитектура:** Core (Go) — парсинг, API, очереди, уведомления. AI Service (Python) — embeddings, matching. Crawler не вызывает LLM.

## Требования

- **Python:** `>=3.11` (для `ai-service`).
- **Go (security baseline):** `>=1.25.8` (рекомендуется запускать команды через `GOTOOLCHAIN=go1.25.8`).

## Локальный Toolchain

- В `backend/go.mod` зафиксирован `toolchain go1.25.8`, поэтому для повторяемых запусков используйте `GOTOOLCHAIN=go1.25.8`.
- Для `ai-service` и проверок (`pytest`, `pip-audit`, coverage) нужен Python `>=3.11`.

Быстрая проверка локального окружения:
```bash
go version
GOTOOLCHAIN=go1.25.8 go version
python3.11 --version
```

Рекомендуемые переменные окружения для локальной работы:
```bash
export GOTOOLCHAIN=go1.25.8
export PYTHON_BIN=python3.11
```

## Быстрый старт

```bash
# 1. Скопировать конфиг и задать переменные
cp .env.example .env
# Отредактировать .env: POSTGRES_PASSWORD, DATABASE_URL, REDIS_URL, API_AUTH_TOKEN, API_USER_HMAC_SECRET, TELEGRAM_BOT_TOKEN
# Если production webhook уже использует этот token, задайте LOCAL_TELEGRAM_BOT_TOKEN для локального polling/smoke.

# 2. Запустить core (always-on: PostgreSQL, Redis, backend-migrate, API)
docker compose up -d

# (опционально) локально включить Telegram-бота
docker compose --profile bot up -d telegram-bot

# (опционально) включить вторичные воркеры:
# backend-crawler, backend-notifier, ai-service, ai-ac-consumer, ollama
docker compose --profile workers up -d

# (опционально) точечно включить только user-embed consumer
docker compose --profile ai-user-embed up -d ai-user-embed

# user-rematch теперь входит в compose по умолчанию; при необходимости можно перезапустить его отдельно
docker compose up -d ai-user-rematch

# 3. Написать боту в Telegram: /start → /profile Ваш профиль
# Project-уведомления идут в chat_id пользователя из таблицы users.
# TELEGRAM_ID из .env нужен для локальных smoke/e2e-проверок и fallback Alertmanager,
# если не задан ALERTMANAGER_TELEGRAM_CHAT_ID.
#
# UX команды /profile:
# - /profile без текста переводит в двухшаговый режим: бот просит прислать профиль следующим сообщением.
# - пустое follow-up сообщение не игнорируется: бот отвечает, что пустой профиль не сохранится.
# - при недоступном backend бот явно сообщает о временной недоступности и просит повторить позже.

# 4. E2E-проверка (опционально)
./scripts/e2e_test.sh
```

Если нужен локальный pytest для `telegram-bot` без системной установки пакетов:
```bash
./scripts/bootstrap_telegram_bot_venv.sh
./scripts/pytest_telegram_bot.sh -q
```

Если нужен локальный pytest для `browser-service` и dockerized smoke:
```bash
./scripts/bootstrap_browser_service_venv.sh
./scripts/pytest_browser_service.sh -q
./scripts/browser_service_integration_smoke.sh
```

Если нужен локальный pytest для `ai-service` без системной установки пакетов:
```bash
./scripts/bootstrap_ai_venv.sh
./scripts/pytest_ai.sh -q
```

Миграции применяются автоматически отдельным one-shot сервисом `backend-migrate` (остальные backend-сервисы стартуют с `RUN_MIGRATIONS=0`). Подробнее: `docs/e2e.md`.

## Operations

- Monitoring, alerts, incident runbook, DR/backup/restore: `docs/operations.md`
- Release checklist: `docs/operations.md` (`0) Release Checklist`)
- Monitoring stack (Prometheus + Alertmanager + Redis/Postgres exporters): `docker-compose.monitoring.yml`
- Monitoring config and alert rules: `monitoring/prometheus/prometheus.yml`, `monitoring/prometheus/alerts.yml`
- Product analytics dashboard: `monitoring/grafana/dashboards/product-analytics.json`
- Backup script: `scripts/backup_postgres.sh`
- Restore script: `scripts/restore_postgres.sh`

## Deployment Profiles

- `docker-compose.yml` — локальный dev-профиль (включает локальные PostgreSQL/Redis и допускает `sslmode=disable`, `redis://`, `http://`).
  Вторичные воркеры (`backend-crawler`, `backend-notifier`, `ai-service`, `ai-ac-consumer`) вынесены в profile `workers` и запускаются on-demand.
  Локальный `telegram-bot` вынесен в profile `bot`; если бот уже живет отдельно, например на Railway, этот profile локально можно не запускать.
  Для точечного запуска отдельный profile сохранён только у `ai-user-embed`.
- `docker-compose.prod.yml` — production-профиль (`APP_ENV=production`, TLS обязателен).
  `ai-user-embed`, `ai-user-rematch` и `ai-ac-consumer` входят по умолчанию.
  `telegram-bot` публикуется только на loopback (`BOT_BIND_IP`/`BOT_PORT`), поэтому production webhook должен идти через host reverse proxy на `WEBHOOK_URL -> 127.0.0.1:${BOT_PORT}`.
  Готовый nginx-конфиг для VPS-схемы см. в `docs/vps_deploy.md`.
- `docker-compose.ssl.yml` — VPS-overlay поверх `docker-compose.prod.yml`.
  Подключает внешнюю сеть `infra_default` (отдельный инфра-compose с postgres + redis) и пробрасывает самоподписанный CA-сертификат во все контейнеры через `SSL_CERT_FILE`.
  Запуск: `docker compose -f docker-compose.prod.yml -f docker-compose.ssl.yml --env-file .env.production up -d`.
  Подробнее: `docs/vps_deploy.md`.
- `docker-compose.monitoring.yml` — профиль мониторинга (Prometheus + Alertmanager, profile `monitoring`).
  По умолчанию он заточен под локальный dev-compose, а для production переопределяется env-переменными scrape/DB endpoints.

### Monitoring (Docker Compose)

```bash
# Alertmanager получает секреты из env:
# - ALERTMANAGER_TELEGRAM_BOT_TOKEN (или TELEGRAM_BOT_TOKEN)
# - ALERTMANAGER_TELEGRAM_CHAT_ID (рекомендуется отдельный канал/ops-chat)
# - если ALERTMANAGER_TELEGRAM_CHAT_ID не задан, используется TELEGRAM_ID
# - ALERTMANAGER_SLACK_WEBHOOK_URL (опционально)
# Не сохраняйте эти значения в plaintext-файлы monitoring/alertmanager/secrets/*
# Очистка legacy-файлов (если были): rm -f monitoring/alertmanager/secrets/telegram_bot_token monitoring/alertmanager/secrets/telegram_chat_id monitoring/alertmanager/secrets/slack_webhook_url

# Поднять приложение + monitoring stack
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml --profile monitoring up -d

# Prometheus UI
# http://127.0.0.1:9090

# Alertmanager UI
# http://127.0.0.1:9093

# Grafana UI
# http://127.0.0.1:3000
```

Опционально можно переопределить источники exporter-метрик:
- `REDIS_EXPORTER_REDIS_ADDR` (по умолчанию `redis://redis:6379`)
- `REDIS_EXPORTER_CHECK_KEYS` (по умолчанию ключи очередей `ai-process/user-embed/user-rematch/ac-batch/match-notify` + `:processing/:dlq`)

Для production monitoring вместе с `docker-compose.prod.yml` переопредели:
- `BACKEND_API_METRICS_TARGET=backend-api:8443`
- `BACKEND_API_METRICS_SCHEME=https`
- `BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY=true`
- `POSTGRES_EXPORTER_DATA_SOURCE_NAME=postgresql://...?...sslmode=require`
- `GRAFANA_POSTGRES_HOST`, `GRAFANA_POSTGRES_PORT`, `GRAFANA_POSTGRES_SSLMODE=require`

Пример production-запуска monitoring stack:
```bash
docker compose --env-file .env.production \
  -f docker-compose.prod.yml \
  -f docker-compose.monitoring.yml \
  --profile monitoring up -d
```

После старта monitoring-профиля в Grafana автоматически появляются:
- Prometheus datasource
- PostgreSQL datasource `Product Analytics Postgres`
- SQL dashboard `SiteParser — Product Analytics`

Дашборд отвечает на базовые продуктовые вопросы:
- сколько пользователей зарегистрировалось сегодня;
- какой процент пользователей заполнил профиль;
- какая конверсия из `notification_sent` в `feedback_submitted`;
- какая биржа даёт лучшие матчи по фидбеку.

Источником для этих панелей служит таблица `product_events`, которая заполняется backend API и notifier. Сейчас пишутся события:
- `user_registered`
- `profile_updated`
- `profile_completed`
- `preferences_updated`
- `notify_hour_updated`
- `feedback_submitted`
- `notification_sent`

### Production (Docker Compose)

```bash
# 1. Подготовить production env
cp .env.production.example .env.production
# Отредактировать .env.production: DATABASE_URL (sslmode=require), REDIS_URL (rediss://), API_URL (https://),
# API_TLS_CERT_HOST_PATH/API_TLS_KEY_HOST_PATH и секреты.

# 2. Запуск production-профиля
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

`ai-user-embed`, `ai-user-rematch` и `ai-ac-consumer` входят в production compose по умолчанию (без отдельного profile).

В production compose **не** поднимает локальные PostgreSQL/Redis контейнеры: используются внешние managed endpoints.

### Railway (монорепо)

Сборка идёт через **Railpack**. Чтобы он видел Go-проект, в настройках сервиса **обязательно** задай:

- **Settings → Build → Root Directory:** `backend` (без слэша в конце).

Без этого Railpack анализирует корень репо, не находит `go.mod` и падает с «could not determine how to build». Конфиг сборки и старта — в `backend/railpack.json`. Watch paths можно задать `backend/**`, чтобы деплой триггерился только при изменениях в backend.

Railway backend теперь стартует через `backend/scripts/railway-start.sh`, который прогоняет тот же migration-aware entrypoint, что и Docker image. Для production держите `RUN_MIGRATIONS=1`; если `DATABASE_URL` смотрит в transaction-pooler, задайте отдельный `DATABASE_MIGRATE_URL` на session/direct connection.

### Deploy Pipeline: Preflight + Staging Smoke/E2E Gate

Workflow `.github/workflows/deploy.yml` теперь сначала гоняет быстрый job `release-preflight-gate`, который валидирует production compose/config contract и backend smoke contract ещё до `deploy-staging`.

Перед production release используйте явный checklist из `docs/operations.md` (`0) Release Checklist`): compose config, env validation, staging smoke, health checks, queue drain, rollback drill.

Локально тот же fail-fast набор можно запустить так:

```bash
./scripts/release_preflight_gate.sh
```

Для staging smoke/e2e job `staging-smoke-e2e-gate` нужны:

- `STAGING_SSH_HOST` (обязателен)
- `STAGING_SSH_USER` (обязателен)
- `STAGING_SSH_PRIVATE_KEY` (обязателен)
- `STAGING_DEPLOY_PATH` (обязателен)
- `STAGING_HEALTHCHECK_URL` (обязателен; `http://` или `https://`)
- `STAGING_GATE_API_URL` (рекомендуется; если не задан, используется `API_URL` из `.env.production` на staging-хосте)
- `STAGING_SMOKE_TELEGRAM_ID` (опционально; переопределяет `TELEGRAM_ID`/дефолтный ID в smoke/e2e gate)

Для production health check в том же workflow нужен:
- `PROD_HEALTHCHECK_URL` (обязателен; `http://` или `https://`)

Также на staging-хосте в `.env.production` должны быть заданы:

- `API_AUTH_TOKEN` (обязателен)
- `API_USER_HMAC_SECRET` (обязателен)
- `API_URL` (обязателен, если не задан `STAGING_GATE_API_URL`)

**Локальный запуск без Docker:**
```bash
# PostgreSQL + Redis
docker compose up -d postgres redis

# Backend API
cd backend && go run ./cmd/api

# Crawler (один проход)
cd backend && go run ./cmd/crawler
```

## Конфигурация (.env)

| Переменная | Описание |
|------------|----------|
| `APP_ENV` | Режим приложения: `development` или `production` (в production включаются строгие transport-проверки) |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL (обязателен, не оставлять шаблонное значение) |
| `DATABASE_URL` | Подключение к PostgreSQL (в production только `sslmode=require|verify-ca|verify-full`) |
| `REDIS_URL` | Подключение к Redis (в production только `rediss://` + пароль) |
| `API_AUTH_TOKEN` | Bearer token для API (обязателен) |
| `API_USER_HMAC_SECRET` | HMAC-секрет подписи user-level API-запросов (обязателен) |
| `API_NONCE_TTL_SEC` | TTL (сек) для `X-Request-Nonce` anti-replay; по умолчанию `600` |
| `API_RATE_LIMIT_WINDOW_SEC` | Размер окна rate-limit (сек); по умолчанию `60` |
| `API_RATE_LIMIT_IP_RPM` | Лимит API-запросов на IP в окне; по умолчанию `120` |
| `API_RATE_LIMIT_TG_RPM` | Лимит API-запросов на Telegram ID в окне; по умолчанию `60` |
| `API_ALLOW_REDIS_DEGRADED` | Явный opt-in запуска backend API без Redis (`1=true`) только для development. По умолчанию `0`: при недоступном Redis API завершится с ошибкой (fail-closed). В `APP_ENV=production` значение `1` запрещено |
| `API_TRUSTED_PROXY_CIDRS` | CIDR-allowlist доверенных reverse-proxy (через запятую). Заголовки `X-Forwarded-For`/`X-Real-IP` учитываются только если `RemoteAddr` попадает в этот список |
| `API_TLS_CERT_FILE`/`API_TLS_KEY_FILE` | Путь к TLS-сертификату и ключу API (обязательны в `APP_ENV=production`) |
| `TELEGRAM_BOT_TOKEN` | Основной токен Telegram-бота для production webhook / notifier / Alertmanager fallback |
| `LOCAL_TELEGRAM_BOT_TOKEN` | Dev-only override для локального polling и notifier smoke, чтобы не конфликтовать с production webhook |
| `POLLING_ACTIVE_WEBHOOK_POLICY` | Поведение локального polling при активном webhook на токене: `standby` (по умолчанию) или `ignore` |
| `TELEGRAM_ID` | Личный chat_id для локальных smoke/e2e-проверок и fallback Alertmanager, если `ALERTMANAGER_TELEGRAM_CHAT_ID` не задан |
| `ALERTMANAGER_TELEGRAM_BOT_TOKEN` | Отдельный токен бота для Alertmanager (опционально; иначе используется `TELEGRAM_BOT_TOKEN`) |
| `ALERTMANAGER_TELEGRAM_CHAT_ID` | Отдельный chat_id канала/ops-чата для monitoring alerts (рекомендуется) |
| `PGADMIN_EMAIL` | Логин для optional `pgAdmin` в production compose; используется только для localhost-only доступа через SSH tunnel |
| `PGADMIN_PASSWORD` | Пароль для optional `pgAdmin`; задайте сильное значение (`openssl rand -hex 16`) |
| `NOTIFY_PRO_MAX_PER_DAY` | Предпочтительный суточный лимит уведомлений на пользователя (`backend-notifier`, по умолчанию `5`) |
| `NOTIFY_MAX_PER_DAY` | Legacy fallback для суточного лимита уведомлений (если `NOTIFY_PRO_MAX_PER_DAY` не задан) |
| `API_URL` | URL backend API (в production для telegram-bot только `https://`) |
| `POSTGRES_BIND_IP`/`REDIS_BIND_IP`/`API_BIND_IP` | Привязка портов Docker к интерфейсу хоста (по умолчанию `127.0.0.1`; для внешней публикации нужно явно задать, например `0.0.0.0`) |
| `POSTGRES_PORT`/`REDIS_PORT`/`API_PORT` | Порты публикации на хосте (`POSTGRES_PORT` по умолчанию `55432` для локального dev/integration) |
| `CRAWL_LIST_URL` | URL страницы проектов (по умолчанию Kwork) |
| `CRAWL_RATE_SEC` | Интервал между запросами (по умолчанию 15) |
| `CRAWL_RETRY_MAX_ATTEMPTS` | Количество попыток fetch для `429/5xx` (по умолчанию `3`) |
| `CRAWL_RETRY_BASE_BACKOFF` | Базовый exponential backoff между retry (по умолчанию `1s`) |
| `CRAWL_RETRY_MAX_BACKOFF` | Верхняя граница backoff между retry (по умолчанию `30s`) |
| `DATABASE_MIGRATE_URL` | Отдельный DSN для миграций; в текущем проектном `.env` совпадает с `DATABASE_URL` |
| `PG_POOL_MAX_CONNS`/`PG_POOL_MIN_CONNS`/`PG_POOL_ACQUIRE_TIMEOUT` | Тюнинг postgres pool для backend и `ai-service` |
| `BROWSER_SERVICE_URL` | URL browser render service для crawler; в текущем `.env` используется `http://browser-service:8090` |
| `LLM_PROVIDER` | Провайдер actor в `ai-ac-consumer`: только `ollama` или `gemini`; без него процесс завершится с ошибкой |
| `GEMINI_API_KEY` | Обязателен при `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL`/`GEMINI_ACTOR_MODEL` | Базовая и role-specific Gemini модель actor (`GEMINI_ACTOR_MODEL` имеет приоритет; по умолчанию `gemini-2.0-flash`) |
| `ACTOR_GEMINI_TIMEOUT_SEC` | Таймаут запросов Actor к Gemini (по умолчанию `30`) |
| `EMBEDDING_MODEL` | Модель эмбеддингов `sentence-transformers` (должна совпадать с preloaded моделью в Docker image) |
| `EMBEDDING_MODELS_DIR`/`RERANK_MODELS_DIR` | Каталоги с локально предзагруженными embedding/rerank моделями (по умолчанию `/opt/models`) |
| `EMBEDDING_REQUIRE_LOCAL` | В текущем проектном `.env` стоит `0`: разрешён fallback на локальный cache/remote download; `1` включает strict local-only режим |
| `RERANK_MODEL` | Cross-encoder модель rerank стадии (по умолчанию `BAAI/bge-reranker-base`) |
| `RERANK_REQUIRE_LOCAL` | Принудительно использовать только локальную/bundled rerank модель; если не задан, наследует `EMBEDDING_REQUIRE_LOCAL` |
| `AI_WARMUP_ENABLED` | В текущем проектном `.env` стоит `0`, чтобы снизить peak RAM на старте embedding consumers |
| `OLLAMA_URL` | URL Ollama для actor при `LLM_PROVIDER=ollama` (локально по умолчанию `http://ollama:11434`) |
| `ACTOR_OLLAMA_MODEL` | Модель Ollama для batch explanation generation (по умолчанию `llama3.2:3b-instruct-q4_K_M`) |
| `ACTOR_OLLAMA_TIMEOUT_SEC` | Таймаут запросов Actor к Ollama (по умолчанию `45`) |
| `OLLAMA_REQUIRED` | Если `1`, `ai-ac-consumer` завершится при недоступном Ollama; по умолчанию `0` в dev и `1` в production |
| `AI_METRICS_BIND` | Bind-address для `/metrics` endpoint AI-consumer процессов (по умолчанию `0.0.0.0`) |
| `AI_CONSUMER_METRICS_PORT` | Порт `/metrics` для `ai-service` consumer (по умолчанию `9108`, `0` = выключить exporter) |
| `AI_AC_CONSUMER_METRICS_PORT` | Порт `/metrics` для `ai-ac-consumer` (по умолчанию `9109`, `0` = выключить exporter) |
| `AI_USER_EMBED_METRICS_PORT` | Порт `/metrics` для `ai-user-embed` (по умолчанию `9110`, `0` = выключить exporter) |
| `USER_REMATCH_QUEUE` | Redis-очередь повторного матчинга пользователей (по умолчанию `user-rematch`) |
| `REMATCH_JOBS_DAYS_BACK`/`REMATCH_MAX_JOBS` | Окно поиска и лимит проектов для `ai-user-rematch` (по умолчанию `7` дней и `5` jobs) |
| `AI_USER_REMATCH_HEALTH_PORT` | Внутренний health-port `ai-user-rematch` (по умолчанию `8092`) |
| `AC_BATCH_QUEUE` | Redis-очередь batch-задач финального ранжирования (по умолчанию `ac-batch`) |
| `AC_BATCH_INTERVAL_SEC` | Интервал планировщика batch в `ai-ac-consumer` (по умолчанию `300`) |
| `AC_BATCH_MIN_JOBS`/`AC_BATCH_MAX_JOBS` | Границы размера batch из pending-совпадений (по умолчанию `1` и `20`) |
| `AC_LEASE_TIMEOUT_SEC` | Lease timeout для claim/reclaim строк в `pending_ac_jobs` (по умолчанию `600`) |
| `AC_PENDING_RETENTION_DAYS` | Retention обработанных строк `pending_ac_jobs` (по умолчанию `14`) |
| `AC_PENDING_CLEANUP_INTERVAL_SEC` | Период cleanup processed строк `pending_ac_jobs` (по умолчанию `3600`) |
| `AC_PENDING_METRICS_REFRESH_SEC` | Период обновления gauge-метрик `pending_ac_jobs` (по умолчанию `30`) |
| `AI_AC_BATCH_POP_TIMEOUT_SEC` | BRPOP timeout `ai-ac-consumer` до cap по shutdown grace (по умолчанию `30`) |
| `MAX_MATCHES_PER_JOB` | Размер ANN candidate pool до rerank (по умолчанию `50`) |
| `SIMILARITY_THRESHOLD` | В текущем проектном `.env` используется tuned значение `0.35` (кодовый fallback выше) |
| `RERANK_THRESHOLD` | Минимальный score cross-encoder rerank для downstream scoring (по умолчанию `0.55`) |
| `RERANK_TOP_K` | Сколько кандидатов оставить после cross-encoder rerank (по умолчанию `10`) |

Полный список: `.env.example`. Документация: `docs/env_setup.md`.

## Тесты

```bash
# Полный локальный прогон gate'ов (workflow lint + unit + browser-service + coverage + monitoring + security)
make test-all

# Backend (Go) — покрытие ≥70% по internal/
bash ./scripts/go_test_backend.sh ./... -cover
# С DATABASE_URL: internal packages ~82%

# если в host-окружении недоступны proxy.golang.org/github.com:
BACKEND_GO_TEST_MODE=docker bash ./scripts/go_test_backend.sh ./... -cover

# AI Service (Python) — покрытие ≥70%
cd ai-service
python3 -m pytest --cov=src -q
# ~84%

# или из корня проекта (устойчиво, даже если pytest CLI не в PATH):
./scripts/pytest_ai.sh --cov=src -q
```

## Security Baseline

```bash
# go mod verify
cd backend && go mod verify

# pip audit (требует Python ≥3.11)
cd ai-service && pip-audit -r requirements.txt

# Полная проверка (версии + govulncheck + pip-audit)
./scripts/security_baseline_check.sh

# Если Python 3.11 установлен не как python3.11:
PYTHON_BIN=python3.12 ./scripts/security_baseline_check.sh
```

## Supply Chain Security (CI)

В CI добавлен workflow `.github/workflows/supply-chain-security.yml`:

- `gitleaks` — secret scan по репозиторию.
- `trivy` и `grype` — скан уязвимостей Docker-образов (`backend`, `ai-service`, `telegram-bot`) с fail на `HIGH/CRITICAL`.
- `syft` — генерация SBOM (CycloneDX JSON) для каждого образа.
- `cosign` — keyless-подпись SBOM-артефактов на `push` (через OIDC).

Артефакты (`trivy/grype/sbom/signature`) публикуются в Actions artifacts.

Для загрузки SARIF и keyless-подписи workflow использует permissions:
- `security-events: write`
- `id-token: write`

Примечание:
- На `pull_request` выполняются сканы и SBOM.
- Подпись SBOM выполняется только на `push`.

## Deploy Gates

`Deploy Pipeline` (`.github/workflows/deploy.yml`) ожидает успешные gate’ы на target SHA:

- `Test Gate`
- `Security Baseline`
- `Supply Chain Security`
