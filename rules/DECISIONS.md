# Решения по архитектуре MVP

---

## Назначение системы

**Система предназначена для:**
- автоматического обнаружения новых фриланс-проектов
- их анализа
- отправки пользователю только релевантных заказов

**Это не:**
- биржа фриланса
- маркетплейс
- автоотклик
- инструмент подачи заявок

**Сервис только:** находит → анализирует → ранжирует → уведомляет.

---

## Главный KPI

**Система считается работающей, если:**
- пользователь получает **≤ 5 уведомлений в день**
- и **большинство из них релевантны**

**Приоритет оптимизации:** релевантность, а не количество источников.

---

## Ключевые анти-ошибки

**Сервис не должен:**
- превращаться в агрегатор вакансий
- делать массовую рассылку
- парсить без задержек (rate limit обязателен)
- писать скрипты обхода логина
- генерировать автоотклики

**Проект — это персональный фильтр проектов, не автоматический фриланс-бот.**

---

## Архитектурный принцип: два сервиса

| Core (Go) | AI Service (Python) |
|-----------|---------------------|
| Парсинг, хранение, API, очереди, уведомления | Обработка текста, классификация, embeddings, matching |

**❗ Запрещено смешивать AI-логику с crawler. Crawler не должен вызывать LLM.**

---

## 1. Площадка для парсинга: **Kwork.ru**

**Почему:**
- Публичный доступ к списку проектов без авторизации (`kwork.ru/projects`)
- Категории: Разработка и IT, Дизайн, Тексты, SEO и др.
- Русскоязычный рынок
- Есть примеры парсеров (например, kwork-parser на GitHub)
- Фриланс.ру и FL.ru требуют логин для просмотра проектов

**Структура:**
- Список: `https://kwork.ru/projects`
- Фильтр по категориям: `?fc=39` (ID категории)
- Детали проекта: `https://kwork.ru/projects/{id}/view`

**Риск:** Вёрстка может меняться → Extractor вынести в отдельный модуль.

---

## 2. Очередь: **Redis**

**Почему:**
- Уже в стеке (инфраструктура)
- Не нужен отдельный брокер (RabbitMQ/Kafka)
- Достаточно для MVP по объёму и надёжности

**Реализация:**
- **Go (producer):** `go-redis` + Redis List (`LPUSH` / `BRPOP`) или [Asynq](https://github.com/hibiken/asynq) для задач
- **Python (consumer):** `redis-py` + `BRPOP` или [RQ](https://python-rq.org/) (Redis Queue)

**Структура очереди:**
```
Queue: ai-process
Payload: {"job_id": 123}
```

---

## 3. Embeddings: **paraphrase-multilingual-MiniLM-L12-v2**

**Почему:**
- Бесплатная, локальная
- Поддержка русского
- 384 измерения — умеренный размер для pgvector
- Работает через sentence-transformers

**Альтернативы (если понадобится):**
- `intfloat/multilingual-e5-small` — лучше для retrieval
- `sentence-transformers/static-similarity-mrl-multilingual-v1` — 1024 dim, выше качество

**Размерность:** 384 → колонка `vector(384)` в pgvector

---

## 4. Загрузка страниц: **HTTP-клиент (без Playwright)**

**Почему:**
- Kwork отдаёт HTML с сервера
- Проще и быстрее, чем браузер
- Меньше ресурсов

**План:** Начать с `net/http` (Go). Подключить Playwright только если появятся страницы с критичным JS-рендерингом.

---

## 5. Схема БД (дополненная)

```sql
-- Проекты (raw + extracted)
CREATE TABLE jobs (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL DEFAULT 'kwork',
    url VARCHAR(500) NOT NULL UNIQUE,
    external_id VARCHAR(100),
    title TEXT NOT NULL,
    description TEXT,
    budget VARCHAR(100),
    skills TEXT[],
    posted_at TIMESTAMPTZ,
    raw_html TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Embeddings (отдельная таблица для гибкости)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE job_embeddings (
    job_id BIGINT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    embedding vector(384),
    ai_metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Пользователи
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    profile_text TEXT,
    embedding vector(384),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- История уведомлений (дедупликация + аналитика)
CREATE TABLE notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    job_id BIGINT REFERENCES jobs(id),
    match_score FLOAT,
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, job_id)
);

CREATE INDEX idx_jobs_url ON jobs(url);
CREATE INDEX idx_jobs_posted_at ON jobs(posted_at DESC);
CREATE INDEX idx_job_embeddings_vector ON job_embeddings USING ivfflat (embedding vector_cosine_ops);
```

---

## 6. Matching — embedding similarity

**Подбор выполняется по embedding similarity, а не по ключевым словам.**

| Использовать | Запрещено |
|--------------|-----------|
| Semantic similarity (cosine similarity) | Keyword filtering как основной механизм |
| pgvector `<=>` | Фильтр по точному совпадению слов |

- **Метрика:** cosine similarity (pgvector `<=>`)
- **Порог:** 0.65–0.75 (подбирать на тестовых данных)
- **Ранжирование:** сортировка по `1 - (embedding <=> user_embedding)` DESC

---

## 7. Telegram

- **Bot** — тонкий клиент: только `API request → показывает результат`. Не хранит данные, не содержит бизнес-логики, не выполняет matching.
- **Backend** — регистрация, профиль, matching, отправка уведомлений (Notifier → Telegram API).
- Команды: `/start` → POST /users, `/profile <текст>` → PUT /profile
- Лимит уведомлений: не чаще 1 в 5 минут, не более 5 в день на пользователя

---

## 8. Конфигурация (env)

```
# Crawler
CRAWL_RATE_SEC=15
KWORK_BASE_URL=https://kwork.ru

# Redis
REDIS_URL=redis://localhost:6379/0
AI_QUEUE=ai-process

# DB
DATABASE_URL=postgres://...

# AI
EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2

# Matching
SIMILARITY_THRESHOLD=0.7
MAX_NOTIFICATIONS_PER_DAY=5

# Telegram
TELEGRAM_BOT_TOKEN=...
```

---

## Итог

| Решение        | Выбор                          |
|----------------|--------------------------------|
| Площадка       | Kwork.ru                       |
| Очередь        | Redis (List или Asynq/RQ)      |
| Embeddings     | paraphrase-multilingual-MiniLM-L12-v2 |
| Загрузка       | HTTP (без Playwright)          |
| Similarity     | cosine, порог 0.7              |
| Размерность    | 384                            |

Можно переходить к реализации.
