# Site Parser for Freelance

Автоматическое обнаружение новых фриланс-проектов, их анализ и отправка пользователю только релевантных заказов.

**Ветки:** `develop` — основная разработка, `main` — стабильный релиз.

**Сервис только:** находит → анализирует → ранжирует → уведомляет.

**Это не:** биржа фриланса, маркетплейс, автоотклик, инструмент подачи заявок.

**KPI:** ≤ 5 уведомлений в день, большинство релевантны. Оптимизировать релевантность, а не количество источников.

**Не:** агрегатор вакансий, массовая рассылка, парсинг без задержек, обход логина, автоотклики.

**Matching:** embedding similarity (cosine), не keyword filtering.

**Архитектура:** Core (Go) — парсинг, API, очереди, уведомления. AI Service (Python) — embeddings, matching. Crawler не вызывает LLM.

## Быстрый старт

```bash
# 1. Скопировать конфиг и задать переменные
cp .env.example .env
# Отредактировать .env (DATABASE_URL, пароли и т.д.)

# 2. Запустить PostgreSQL + Redis
docker compose up -d

# 3. Проверить миграции (опционально)
./scripts/verify_migrations.sh

# 4. Запустить Crawler (один проход)
cd backend && go run ./cmd/crawler/
# или собрать бинарник:
cd backend && go build -o bin/crawler ./cmd/crawler/ && ./bin/crawler
```

Миграции применяются автоматически при первом запуске PostgreSQL (volume пустой).

## Конфигурация (env)

| Переменная | Описание |
|------------|----------|
| `DATABASE_URL` | Подключение к PostgreSQL (обязательно для crawler) |
| `CRAWL_LIST_URL` | URL страницы со списком проектов (по умолчанию `KWORK_BASE_URL/projects`) |
| `KWORK_BASE_URL` | Базовый URL Kwork (по умолчанию `https://kwork.ru`) |
| `CRAWL_RATE_SEC` | Интервал между запросами в секундах (по умолчанию 15) |

## Тесты

```bash
cd backend

# Unit-тесты (http, kwork)
go test ./internal/adapter/http/ ./internal/adapter/kwork/ -v -cover

# Интеграционные тесты Postgres (без Docker — пропуск)
source ../.env && go test ./internal/adapter/postgres/ -v

# Интеграционные тесты Postgres (с Docker — testcontainers)
go test -tags=integration ./internal/adapter/postgres/ -v -timeout 120s

# Все тесты
go test ./... -cover
```
