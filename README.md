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
- **Go (security baseline):** `>=1.25.7` (рекомендуется запускать команды через `GOTOOLCHAIN=go1.25.7`).

## Локальный Toolchain

- В `backend/go.mod` зафиксирован `toolchain go1.25.7`, поэтому для повторяемых запусков используйте `GOTOOLCHAIN=go1.25.7`.
- Для `ai-service` и проверок (`pytest`, `pip-audit`, coverage) нужен Python `>=3.11`.

Быстрая проверка локального окружения:
```bash
go version
GOTOOLCHAIN=go1.25.7 go version
python3.11 --version
```

Рекомендуемые переменные окружения для локальной работы:
```bash
export GOTOOLCHAIN=go1.25.7
export PYTHON_BIN=python3.11
```

## Быстрый старт

```bash
# 1. Скопировать конфиг и задать переменные
cp .env.example .env
# Отредактировать .env: POSTGRES_PASSWORD, DATABASE_URL, REDIS_URL, API_AUTH_TOKEN, API_USER_HMAC_SECRET, TELEGRAM_BOT_TOKEN

# 2. Запустить core (always-on: PostgreSQL, Redis, backend-migrate, API, Telegram-бот)
docker compose up -d

# (опционально) включить вторичные воркеры:
# backend-crawler, backend-notifier, ai-service, ai-ac-consumer, ollama
docker compose --profile workers up -d

# (опционально) точечно включить только user-embed consumer
docker compose --profile ai-user-embed up -d ai-user-embed

# 3. Написать боту в Telegram: /start → /profile Ваш профиль
# Уведомления придут на TELEGRAM_ID из .env

# 4. E2E-проверка (опционально)
./scripts/e2e_test.sh
```

Миграции применяются автоматически отдельным one-shot сервисом `backend-migrate` (остальные backend-сервисы стартуют с `RUN_MIGRATIONS=0`). Подробнее: `docs/e2e.md`.

## Operations

- Monitoring, alerts, incident runbook, DR/backup/restore: `docs/operations.md`
- Monitoring stack (Prometheus + Alertmanager + Redis/Postgres exporters): `docker-compose.monitoring.yml`
- Monitoring config and alert rules: `monitoring/prometheus/prometheus.yml`, `monitoring/prometheus/alerts.yml`
- Backup script: `scripts/backup_postgres.sh`
- Restore script: `scripts/restore_postgres.sh`

## Deployment Profiles

- `docker-compose.yml` — локальный dev-профиль (включает локальные PostgreSQL/Redis и допускает `sslmode=disable`, `redis://`, `http://`).
  Вторичные воркеры (`backend-crawler`, `backend-notifier`, `ai-service`, `ai-ac-consumer`) вынесены в profile `workers` и запускаются on-demand.
  Для точечного запуска `ai-user-embed` доступен отдельный profile `ai-user-embed`.
- `docker-compose.prod.yml` — production-профиль (только внешние TLS endpoints, `APP_ENV=production`).
- `docker-compose.monitoring.yml` — профиль мониторинга (Prometheus + Alertmanager, profile `monitoring`).

### Monitoring (Docker Compose)

```bash
# Alertmanager получает секреты из env:
# - ALERTMANAGER_TELEGRAM_BOT_TOKEN (или TELEGRAM_BOT_TOKEN)
# - ALERTMANAGER_TELEGRAM_CHAT_ID (или TELEGRAM_ID)
# - ALERTMANAGER_SLACK_WEBHOOK_URL (опционально)
# Не сохраняйте эти значения в plaintext-файлы monitoring/alertmanager/secrets/*
# Очистка legacy-файлов (если были): rm -f monitoring/alertmanager/secrets/telegram_bot_token monitoring/alertmanager/secrets/telegram_chat_id monitoring/alertmanager/secrets/slack_webhook_url

# Поднять приложение + monitoring stack
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml --profile monitoring up -d

# Prometheus UI
# http://127.0.0.1:9090

# Alertmanager UI
# http://127.0.0.1:9093
```

Опционально можно переопределить источники exporter-метрик:
- `REDIS_EXPORTER_REDIS_ADDR` (по умолчанию `redis://redis:6379`)
- `REDIS_EXPORTER_CHECK_KEYS` (по умолчанию ключи очередей `ai-process/user-embed/ac-batch/match-notify` + `:processing/:dlq`)

### Production (Docker Compose)

```bash
# 1. Подготовить production env
cp .env.production.example .env.production
# Отредактировать .env.production: DATABASE_URL (sslmode=require), REDIS_URL (rediss://), API_URL (https://),
# API_TLS_CERT_HOST_PATH/API_TLS_KEY_HOST_PATH и секреты.

# 2. Запуск production-профиля
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build

# (опционально) включить user-embed consumer
docker compose --env-file .env.production -f docker-compose.prod.yml --profile ai-user-embed up -d ai-user-embed
```

`ai-ac-consumer` входит в production compose по умолчанию (без отдельного profile).

В production compose **не** поднимает локальные PostgreSQL/Redis контейнеры: используются внешние managed endpoints.

### Railway (монорепо)

Сборка идёт через **Railpack**. Чтобы он видел Go-проект, в настройках сервиса **обязательно** задай:

- **Settings → Build → Root Directory:** `backend` (без слэша в конце).

Без этого Railpack анализирует корень репо, не находит `go.mod` и падает с «could not determine how to build». Конфиг сборки и старта — в `backend/railpack.json`. Watch paths можно задать `backend/**`, чтобы деплой триггерился только при изменениях в backend.

### Deploy Pipeline: Secrets for Staging Smoke/E2E Gate

Для workflow `.github/workflows/deploy.yml` (job `staging-smoke-e2e-gate`) нужны:

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
| `TELEGRAM_BOT_TOKEN` | Токен бота для уведомлений |
| `TELEGRAM_ID` | Ваш chat_id (уведомления придут сюда) |
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
| `EMBEDDING_MODEL` | Модель эмбеддингов `sentence-transformers` (должна совпадать с preloaded моделью в Docker image) |
| `EMBEDDING_REQUIRE_LOCAL` | `1` (рекомендуется): запрещает runtime-загрузку модели из сети; сервис падает, если модель не найдена локально/в `EMBEDDING_MODELS_DIR` |
| `OLLAMA_URL` | URL Ollama для AI classifier (локально по умолчанию `http://ollama:11434`) |
| `OLLAMA_MODEL` | Модель Ollama для classifier (по умолчанию `llama3.2:3b-instruct-q4_K_M`) |
| `OLLAMA_TIMEOUT_SEC` | Таймаут запроса к Ollama в секундах (по умолчанию `30`) |
| `AI_METRICS_BIND` | Bind-address для `/metrics` endpoint AI-consumer процессов (по умолчанию `0.0.0.0`) |
| `AI_CONSUMER_METRICS_PORT` | Порт `/metrics` для `ai-service` consumer (по умолчанию `9108`, `0` = выключить exporter) |
| `AI_AC_CONSUMER_METRICS_PORT` | Порт `/metrics` для `ai-ac-consumer` (по умолчанию `9109`, `0` = выключить exporter) |
| `AC_BATCH_QUEUE` | Redis-очередь batch-задач Actor-Critic (по умолчанию `ac-batch`) |
| `AC_BATCH_INTERVAL_SEC` | Интервал планировщика batch в `ai-ac-consumer` (по умолчанию `300`) |
| `AC_BATCH_MIN_JOBS`/`AC_BATCH_MAX_JOBS` | Границы размера batch из pending-совпадений (по умолчанию `1` и `20`) |
| `AC_SCORE_THRESHOLD` | Порог Critic (0..10) для отправки batch-нотификации (по умолчанию `5.0`) |
| `AC_MAX_ATTEMPTS` | Максимум итераций Actor-Critic loop на один batch (по умолчанию `3`) |
| `AC_MAX_JOBS_PER_SELECTION` | Сколько объявлений Actor возвращает за итерацию (по умолчанию `5`) |
| `ACTOR_OLLAMA_MODEL`/`CRITIC_OLLAMA_MODEL` | Модели Ollama для Actor/Critic (по умолчанию `llama3.2:3b-instruct-q4_K_M`) |
| `ACTOR_OLLAMA_TIMEOUT_SEC`/`CRITIC_OLLAMA_TIMEOUT_SEC` | Таймауты запросов Actor/Critic к Ollama (по умолчанию `45` и `30`) |

Полный список: `.env.example`. Документация: `docs/env_setup.md`.

## Тесты

```bash
# Полный локальный прогон gate'ов (workflow lint + unit + coverage + monitoring + security)
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
