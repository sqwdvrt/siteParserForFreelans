# Site Parser for Freelance — Полная документация

> Система автоматического поиска, ранжирования и доставки фриланс-проектов пользователям через Telegram.

---

## Содержание

1. [Архитектура](#1-архитектура)
2. [Сервисы](#2-сервисы)
3. [Сквозной поток данных](#3-сквозной-поток-данных)
4. [База данных](#4-база-данных)
5. [Redis-очереди](#5-redis-очереди)
6. [API](#6-api)
7. [Безопасность](#7-безопасность)
8. [Конфигурация](#8-конфигурация)
9. [Запуск и деплой](#9-запуск-и-деплой)
10. [Мониторинг](#10-мониторинг)
11. [Бизнес-логика подробно](#11-бизнес-логика-подробно)

---

## 1. Архитектура

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                        ПОЛЬЗОВАТЕЛЬ                              │
 │                  (Telegram: /start, /profile)                    │
 └───────────────────────────┬──────────────────────────────────────┘
                             │
                    ┌────────▼─────────┐
                    │  telegram-bot    │  Python · getUpdates polling
                    │  (thin client)   │
                    └────────┬─────────┘
                             │ HTTP + HMAC
                    ┌────────▼─────────┐
                    │  backend-api     │  Go · REST · :8080
                    │  (Auth, RLS)     │
                    └──┬───────────┬───┘
                       │           │
          ┌────────────▼──┐   ┌───▼────────────┐
          │  PostgreSQL   │   │     Redis       │
          │  (pgvector)   │   │  (queues+cache) │
          └────────────┬──┘   └───┬─────────────┘
                       │          │
       ┌───────────────┼──────────┼────────────────────────┐
       │               │          │                        │
┌──────▼──────┐  ┌─────▼──────┐  │  ┌────────────────┐    │
│  backend-   │  │ ai-service │  │  │backend-notifier│    │
│  crawler    │  │ (consumer) │◄─┘  │  (dispatcher)  │    │
│  (scraper)  │  │            │     │                │    │
└──────┬──────┘  └─────┬──────┘     └────────────────┘    │
       │                │                    ▲             │
┌──────▼──────┐  ┌──────▼──────┐            │             │
│  browser-   │  │  ai-ac-     │────────────┘             │
│  service    │  │  consumer   │  (final scoring batch)    │
│ (Playwright)│  │             │                          │
└─────────────┘  └─────┬───────┘                          │
                        │                                  │
                 ┌──────▼──────┐                          │
                 │   Ollama    │  LLM (llama3.2:3b)       │
                 │  (LLM API)  │                          │
                 └─────────────┘                          │
                                                          │
                    ai-user-embed ◄───────────────────────┘
                    (profile embeddings)
                           │
                           ▼
                    ai-user-rematch
                  (replay existing jobs)
```

**Принцип:** основной межсервисный обмен идёт через PostgreSQL + Redis-очереди. Исключение: `backend-crawler` обращается к `browser-service` по HTTP (`/render`) для JS-рендеринга страниц.

---

## 2. Сервисы

### 2.1 backend-api (Go)

**Роль:** единственная точка входа для telegram-бота. Авторизация, валидация, запись данных.

**Порт:** 8080 (http) / 8443 (https в production)

**Что делает:**
- Регистрирует пользователей (POST /users)
- Обновляет профиль фрилансера → кладёт задачу на переиндексацию в очередь `user-embed`
- Принимает 👍/👎 фидбек → пишет в `user_feedback`
- Хранит и отдаёт пользовательские предпочтения (ключевые слова, бюджет, источники)
- Управляет часом дайджеста (`notify_hour`) для Pro-пользователей
- Пишет продуктовые события в `product_events` для SQL-аналитики Grafana
- Отдаёт метрики Prometheus, healthz/readyz

**Безопасность:** Bearer token + HMAC-SHA256 подпись каждого запроса (защита от CSRF/replay).

---

### 2.2 backend-crawler (Go)

**Роль:** периодически парсит фриланс-биржи, сохраняет проекты в БД.

**Запуск:** cron (`CRAWL_CRON`, по умолчанию `0 5 * * *` — раз в сутки в 05:00 UTC).

**Источники (настраиваются через `ENABLED_SOURCES`):**

| Источник | Метод получения HTML |
|----------|----------------------|
| Kwork | browser-service (JS рендеринг через Playwright) |
| FL.ru | plain HTTP GET |
| Freelancehunt | plain HTTP GET |

**Цикл:**
1. Fetch HTML → extract (title, description, budget, skills, URL)
2. `INSERT INTO jobs ... ON CONFLICT DO NOTHING` (дедупликация по URL)
3. Новые задания → `LPUSH ai-process`

**Резильентность:** circuit breaker (3 ошибки → open, 1 мин cooldown), exponential backoff retry (до 3 попыток, backoff 1s–30s), timeout per run.

---

### 2.3 ai-service / consumer (Python)

**Роль:** основной движок матчинга. Читает `ai-process`, находит подходящих пользователей.

**Цикл обработки одного задания:**

```
BRPOP ai-process
    → загрузить job из БД
    → sentence-transformers: generate embedding (384-dim)
    → pgvector HNSW: top-N пользователей по cosine similarity
    → cross-encoder rerank: (profile_text, job.title + job.description)
    → filter: rerank_score >= RERANK_THRESHOLD
    → сохранить top-K в pending_ac_jobs
    → накопить batch → LPUSH ac-batch
```

**Модель:** `all-MiniLM-L6-v2` (sentence-transformers, 384 dim)
**Reranker:** `BAAI/bge-reranker-base`
**Индекс:** pgvector HNSW (cosine distance), probes=10

---

### 2.4 ai-ac-consumer (Python, final scoring)

**Роль:** финальный отбор и ранжирование батча проектов для пользователя с одним LLM-вызовом только на `why_it_fits`.

**Алгоритм:**
1. Собирает `pending_ac_jobs` за интервал (`AC_BATCH_INTERVAL_SEC`)
2. Считает `final_score` как мультипликативный скор от `rerank_score` с учётом `feedback_bonus`, `preference_multiplier`, `time_decay_multiplier`, `competition_multiplier`
3. Фильтрует кандидатов по `rerank_score >= RERANK_THRESHOLD`, берёт top-5 по `final_score`
4. **Actor** (LLM): одним batched-запросом генерирует `why_it_fits` для выбранных проектов
5. `INSERT INTO notifications (status='pending', why_it_fits=...)`
6. `LPUSH match-notify`

Корректировка по фидбеку применяется в двух местах:
- в `ai-service` (при job→user матчинге) через `adjust_candidates`;
- в `ai-ac-consumer` как `feedback_bonus` при финальном скоринге.

---

### 2.5 ai-user-embed (Python)

**Роль:** генерирует векторное представление профиля пользователя.

**Триггер:** после `PUT /users/:id/profile` backend-api кладёт задачу в очередь `user-embed`.

**Цикл:**
```
BRPOP user-embed
    → загрузить profile_text из users
    → sentence-transformers: embed(profile_text)
    → UPDATE user_embeddings SET embedding = [...]
```

После обновления эмбеддинга пользователь автоматически начнёт получать более релевантные проекты при следующем матчинге.

---

### 2.6 ai-user-rematch (Python)

**Роль:** повторный матчинг пользователя по всем существующим проектам.

**Когда:** при значительном изменении профиля или настроек предпочтений.

**Цикл:**
```
BRPOP user-rematch
    → загрузить user.embedding
    → pgvector: найти топ-K проектов за последние N дней
    → скорректировать кандидатов сигналом пользовательского фидбека
    → LPUSH match-notify (single-candidate payloads)
```

---

### 2.7 backend-notifier (Go)

**Роль:** отправляет уведомления в Telegram из очереди `match-notify`.

**Два режима работы:**

**Режим 1: Немедленная доставка** (для free-пользователей)
```
BRPOP match-notify
    → rate limit: ≤5 мин с последней отправки?
    → daily limit: ≤5 уведомлений сегодня?
    → Telegram Bot API: sendMessage
    → MarkSent в notifications
```

**Режим 2: Дайджест** (для Pro-пользователей)
- Cron `DIGEST_CRON` (по умолчанию `0 * * * *` — каждый час)
- Определяет текущий московский час
- Берёт Pro-пользователей с `notify_hour = текущий_час`
- Для каждого: `GetPendingForUser` → батч-отправка → MarkSent

**Отличие Pro от Free:**
- Free: уведомление сразу при появлении матча (rate limit 5 мин, cap 5/день)
- Pro: все матчи копятся как pending, отправляются одним дайджестом в указанное время

**Резильентность:** Telegram circuit breaker (3 ошибки → 30 с cooldown), retry with backoff, DLQ для неотправленных, read-repair processing-очереди после рестарта.

---

### 2.8 telegram-bot (Python)

**Роль:** тонкий клиент — только UI. Вся логика в backend-api.

**Команды:**

| Команда | Действие |
|---------|----------|
| `/start` | POST /users → регистрация |
| `/profile` или `/profile <текст>` | PUT /users/:id/profile → обновление профиля (с поддержкой двухшагового ввода) |
| `/notify_hour <0-23>` | PUT /users/:id/notify-hour → час дайджеста (Pro) |
| `/help` | Список команд |

**UX ошибок `/profile`:**
- Если пользователь отправил `/profile` без текста, бот переводит диалог в режим ожидания профиля и присылает инструкцию.
- Если в этом режиме пришёл пустой текст, бот отвечает, что пустой профиль не сохраняется.
- Если backend временно недоступен (`network/5xx`), бот отправляет явное сообщение о временной ошибке и предлагает повторить позже.

**Callbacks:**
- 👍 / 👎 на inline-кнопках → POST /users/:id/feedback

**Безопасность бота:**
- Каждый запрос к API подписывается HMAC-SHA256 (telegram_id + body + timestamp + nonce)
- URL allowlist: допускает только разрешённые хосты
- Heartbeat-файл `/tmp/telegram-bot-heartbeat` → Docker healthcheck

---

### 2.9 browser-service (Python)

**Роль:** рендерит JS-тяжёлые страницы (Kwork) через headless Chromium.

**API:** `GET /render?url=<url>`

**Как работает:**
1. Playwright: открывает URL в изолированном контексте (без cookies между запросами)
2. Ждёт networkidle или селектор `.want-card` (карточки проектов)
3. Возвращает полный HTML

---

## 3. Сквозной поток данных

```
[1] ПАРСИНГ
    ─────────
    backend-crawler (cron)
        → Kwork/FL.ru/Freelancehunt → HTML
        → extract: title, description, budget, skills, URL
        → INSERT INTO jobs (ON CONFLICT DO NOTHING)
        → LPUSH ai-process {job_id}

[2] МАТЧИНГ
    ─────────
    ai-service consumer (BRPOP ai-process)
        → embed(job.title + job.description)
        → pgvector ANN: top-N пользователей по cosine similarity
        → cross-encoder rerank: (profile_text, job.title + job.description)
        → filter: rerank_score >= threshold
        → INSERT INTO pending_ac_jobs
        → LPUSH ac-batch {user_id, job_ids[]}

[3] FINAL SCORING
    ─────────────
    ai-ac-consumer (BRPOP ac-batch)
        → final_score = rerank_score * (1+feedback_bonus) * preference_multiplier * time_decay_multiplier * competition_multiplier
        → filter rerank_score >= threshold
        → top-5 по final_score
        → Actor LLM: одним вызовом сгенерировать why_it_fits
        → INSERT INTO notifications (status='pending', why_it_fits)
        → LPUSH match-notify {user_id, jobs[], batch_score}

[4a] ДОСТАВКА (Free-пользователи)
    ────────────────────────────
    backend-notifier (BRPOP match-notify)
        → user.IsPro? → нет → продолжаем
        → rate limit: SentRecently(5 мин)?
        → daily limit: CountToday ≥ 5?
        → Telegram: sendMessage (batch с inline 👍/👎)
        → MarkSent

[4b] ДАЙДЖЕСТ (Pro-пользователи)
    ────────────────────────────
    backend-notifier (cron, каждый час)
        → MSK hour = time.Now().In(Moscow).Hour()
        → SELECT users WHERE is_pro AND notify_hour = MSK_hour
        → для каждого пользователя:
            pending = GetPendingForUser (статус 'pending')
            ограничить до remaining = maxPerDay - CountToday
            загрузить jobs (GetByIDs)
            Telegram: batch sendMessage
            MarkSent

[5] ФИДБЕК
    ────────
    User: нажимает 👍 / 👎 в Telegram
        → telegram-bot: POST /users/:id/feedback {job_id, feedback}
        → backend-api: INSERT INTO user_feedback
        → ai-service: при следующем матчинге — FeedbackSignal корректирует скоры
```

---

## 4. База данных

### 4.1 Product Analytics Events

Для базовой продуктовой аналитики используется таблица `product_events`.

Назначение:
- считать регистрации и заполнение профиля без опоры на технические метрики Prometheus;
- строить SQL-дашборды в Grafana поверх PostgreSQL;
- связывать отправленные уведомления с фидбеком и источником проекта.

Текущие event types:
- `user_registered`
- `profile_updated`
- `profile_completed`
- `preferences_updated`
- `notify_hour_updated`
- `feedback_submitted`
- `notification_sent`

Ключевые поля события:
- `event_type`
- `user_id`
- `job_id`
- `source`
- `properties jsonb`
- `created_at`

### Схема (ключевые таблицы)

#### `jobs`
```sql
id            BIGSERIAL PK
source        TEXT           -- 'kwork' | 'flru' | 'freelancehunt'
external_id   TEXT
url           TEXT UNIQUE
title         TEXT
description   TEXT
budget        TEXT
skills        TEXT[]
posted_at     TIMESTAMPTZ
raw_html      TEXT
created_at    TIMESTAMPTZ DEFAULT NOW()
```

#### `job_embeddings`
```sql
job_id        BIGINT FK jobs.id
embedding     VECTOR(384)    -- pgvector, HNSW index (cosine)
```

#### `users`
```sql
id            BIGSERIAL PK
telegram_id   BIGINT UNIQUE
profile_text  TEXT
is_pro        BOOLEAN DEFAULT FALSE
notify_hour   SMALLINT       -- 0-23 (МСК), NULL = не задан
created_at    TIMESTAMPTZ
updated_at    TIMESTAMPTZ
```

#### `user_embeddings`
```sql
user_id       BIGINT FK users.id
embedding     VECTOR(384)
updated_at    TIMESTAMPTZ
```

#### `user_preferences`
```sql
user_id           BIGINT FK users.id PK
include_keywords  TEXT[]
exclude_keywords  TEXT[]
min_budget        NUMERIC
max_budget        NUMERIC
preferred_sources TEXT[]     -- ['kwork', 'flru', ...]
updated_at        TIMESTAMPTZ
```

#### `notifications`
```sql
id             BIGSERIAL PK
user_id        BIGINT FK users.id
job_id         BIGINT FK jobs.id
match_score    FLOAT          -- косинусное сходство (0–1)
final_score    FLOAT          -- итоговый скор после rerank + embedding + feedback
ranker_version TEXT
reason_codes   TEXT[]
why_it_fits    TEXT           -- объяснение от Actor LLM
status         TEXT           -- 'pending' | 'sent'
sent_at        TIMESTAMPTZ
UNIQUE(user_id, job_id)       -- дедупликация
```

#### `user_feedback`
```sql
id          BIGSERIAL PK
user_id     BIGINT FK users.id
job_id      BIGINT FK jobs.id
feedback    TEXT           -- 'good' | 'bad'
created_at  TIMESTAMPTZ
UNIQUE(user_id, job_id)
```

#### `pending_ac_jobs`
```sql
id             BIGSERIAL PK
user_id        BIGINT FK users.id
job_id         BIGINT FK jobs.id
match_score    FLOAT
raw_similarity FLOAT
final_score    FLOAT
ranker_version TEXT
reason_codes   TEXT[]
queued_at      TIMESTAMPTZ
lease_expires  TIMESTAMPTZ    -- lease для параллельных воркеров
trace_id       TEXT
```

### Миграции

| Файл | Что делает |
|------|-----------|
| `001_init.sql` | Базовая схема: jobs, job_embeddings, users, notifications |
| `002_...` | Индекс (user_id, sent_at) на notifications |
| `003_...` | Колонка status в notifications (pending/sent) |
| `004_...` | Таблица pending_ac_jobs |
| `005_...` | trace_id в pending_ac_jobs |
| `006_...` | queued_at в pending_ac_jobs |
| `007_...` | Индекс для reclaim expired leases |
| `008_...` | Индекс на status в notifications |
| `009_...` | Row-Level Security (RLS) для изоляции данных |
| `010_...` | Таблица user_feedback |
| `011_...` | is_pro, notify_hour в users |
| `012_...` | Таблица ranking_signals (ML сигналы) |
| `013_...` | Таблица user_preferences |
| `014_...` | Таблица user_tag_affinity (аффинность к навыкам) |
| `015_...` | pending_sent_at для дайджеста |
| `016_...` | why_it_fits в notifications |
| `017_notifications_pending_partial_index.sql` | Частичный индекс pending-уведомлений для retry/digest путей |
| `017_rerank_score.sql` | Колонка `rerank_score` в `pending_ac_jobs` |
| `018_hnsw_index_params.sql` | Явные HNSW-параметры индексов для `job_embeddings` и `users.embedding` |
| `019_pending_embed_dispatch.sql` | Outbox-таблицы `pending_user_embeds`/`pending_job_embeds` для deferred Redis dispatch |
| `020_pending_ac_jobs_score_components.sql` | Компоненты скоринга в `pending_ac_jobs` (`feedback_bonus`, `preference_multiplier`) |
| `021_user_job_filter_events.sql` | Таблица `user_job_filter_events` для explainable matching stats |
| `022_product_events.sql` | Таблица `product_events` для продуктовой SQL-аналитики |

**Применение:** `make migrate` (запускает `backend-migrate` контейнер с advisory lock).
Нюанс: в репозитории исторически есть два файла с префиксом `017_*`; это ожидаемо для текущего набора миграций.

---

## 5. Redis-очереди

Все очереди реализованы как надёжные очереди (reliable queue) через LMOVE + processing-list.

| Очередь | Producer | Consumer | Содержимое |
|---------|----------|----------|-----------|
| `ai-process` | backend-crawler | ai-service | `{job_id}` |
| `user-embed` | backend-api | ai-user-embed | `{user_id}` |
| `user-rematch` | ai-user-embed | ai-user-rematch | `{user_id}` |
| `ac-batch` | ai-service | ai-ac-consumer | `{user_id, job_ids[]}` |
| `match-notify` | ai-ac-consumer, ai-user-rematch | backend-notifier | single: `{user_id, job_id, match_score, ...}` или batch: `{user_id, jobs[], batch_score, traceparent}` |
| `{queue}:processing` | consumer | consumer | In-flight messages |
| `{queue}:dlq` | consumer (после N retry) | manual | Dead-letter |

**Read-repair:** при старте каждый consumer переносит застрявшие сообщения из `{queue}:processing` обратно в `{queue}`.

**Дополнительные ключи:**
- `api:nonce:{nonce}` — anti-replay TTL ключи
- `api:ratelimit:ip:{ip}` — rate limit счётчики
- `api:ratelimit:tg:{telegram_id}` — rate limit по Telegram ID

---

## 6. API

**Аутентификация:** все пользовательские эндпоинты требуют:
1. `Authorization: Bearer <API_AUTH_TOKEN>`
2. Заголовки `X-Telegram-ID`, `X-Request-Timestamp`, `X-Request-Nonce`, `X-Request-Signature`

**Подпись запроса (HMAC-SHA256):**
```
signature = HMAC-SHA256(
    key   = API_USER_HMAC_SECRET,
    data  = "{method}\n{path}\n{telegram_id}\n{timestamp}\n{nonce}\n{sha256(body)}"
)
```

### Пользовательские эндпоинты

| Метод | Путь | Тело | Ответ | Описание |
|-------|------|------|-------|----------|
| POST | `/users` | `{telegram_id}` | `{user_id}` | Регистрация |
| PUT | `/users/{id}/profile` | `{profile_text}` | 204 | Обновить профиль + trigger embed |
| PUT | `/users/{id}/notify-hour` | `{hour: 9}` | 204 / 403 | Установить час дайджеста (Pro) |
| GET | `/users/{id}/preferences` | — | `{...prefs}` | Получить предпочтения |
| PUT | `/users/{id}/preferences` | `{include_keywords, ...}` | 204 | Обновить предпочтения |
| POST | `/users/{id}/feedback` | `{job_id, feedback: "good"}` | 204 | 👍/👎 фидбек |

### Admin эндпоинты (только Bearer, без HMAC)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/admin/stats` | Статистика системы |
| GET | `/admin/users` | Список пользователей |
| GET | `/admin/users/{id}` | Данные пользователя |
| DELETE | `/admin/users/{id}` | Удалить пользователя |
| GET | `/admin/jobs` | Список проектов |

### Health

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/healthz` | Liveness (всегда 200) |
| GET | `/readyz` | Readiness (200 если DB+Redis доступны) |
| GET | `/metrics` | Prometheus метрики |

---

## 7. Безопасность

### API
- **HMAC подпись** каждого запроса: telegram_id + body + timestamp + nonce
- **Anti-replay:** nonce TTL 600 с в Redis — каждый запрос одноразовый
- **Rate limiting:** IP (120 req/min), Telegram ID (60 req/min)
- **RLS (Row-Level Security):** PostgreSQL — пользователь видит только свои данные
  - При каждой транзакции: `SET LOCAL app.current_user_id = {id}`

### Transport
- Production: TLS обязателен (API_TLS_CERT_FILE/KEY, sslmode=require, rediss://)
- Development: допускается http/insecure

### Secrets
- Минимальная длина: 16–32 символа в зависимости от секрета
- Проверка энтропии при старте (ValidateSecret)
- Логирование: никогда не логируются токены/пароли (только имя исключения, не message)

### Telegram Bot
- URL allowlist: запросы только к разрешённым хостам
- Safe redirect handler (Playwright + urllib)
- Heartbeat файл: `/tmp/telegram-bot-heartbeat` (Docker healthcheck)

---

## 8. Конфигурация

### Backend-API

| Переменная | Default | Обязательная |
|-----------|---------|:---:|
| `DATABASE_URL` | — | ✓ |
| `REDIS_URL` | `redis://localhost:6379/0` | prod ✓ |
| `API_AUTH_TOKEN` | — | ✓ (≥32 chars) |
| `API_USER_HMAC_SECRET` | — | ✓ (≥32 chars) |
| `APP_ENV` | `development` | |
| `API_ADDR` | `:8080` | |
| `API_TLS_CERT_FILE` | — | prod ✓ |
| `API_TLS_KEY_FILE` | — | prod ✓ |
| `API_NONCE_TTL_SEC` | `600` | |
| `API_RATE_LIMIT_IP_RPM` | `120` | |
| `API_RATE_LIMIT_TG_RPM` | `60` | |
| `USER_EMBED_QUEUE` | `user-embed` | |

### Backend-Crawler

| Переменная | Default | Описание |
|-----------|---------|---------|
| `CRAWL_CRON` | `0 5 * * *` | Расписание парсинга |
| `CRAWL_RATE_SEC` | `15` | Задержка между запросами |
| `CRAWL_RETRY_MAX_ATTEMPTS` | `3` | Кол-во retry |
| `CRAWL_RETRY_BASE_BACKOFF` | `1s` | Начальный backoff |
| `CRAWL_RETRY_MAX_BACKOFF` | `30s` | Максимальный backoff |
| `CRAWL_BREAKER_FAILURE_THRESHOLD` | `3` | Ошибки до open circuit |
| `CRAWL_BREAKER_OPEN_INTERVAL` | `1m` | Время cooldown |
| `CRAWL_RUN_TIMEOUT` | `10m` | Таймаут одного запуска |
| `ENABLED_SOURCES` | `kwork` | Источники через запятую |
| `BROWSER_SERVICE_URL` | — | URL browser-service |

### Backend-Notifier

| Переменная | Default | Описание |
|-----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | — | ✓ (≥20 chars) |
| `MATCH_NOTIFY_QUEUE` | `match-notify` | Имя очереди |
| `NOTIFY_RATE_LIMIT_SEC` | `300` | 5 мин между уведомлениями (free) |
| `NOTIFY_PRO_MAX_PER_DAY` | `5` | Лимит в сутки (pro дайджест) |
| `DIGEST_CRON` | `0 * * * *` | Расписание дайджеста |
| `NOTIFIER_MAX_RETRIES` | `3` | Retry Telegram |
| `NOTIFIER_BREAKER_FAILURE_THRESHOLD` | `3` | Circuit breaker порог |
| `NOTIFIER_BREAKER_OPEN_INTERVAL` | `30s` | Circuit breaker cooldown |

### AI Service

| Переменная | Default | Описание |
|-----------|---------|---------|
| `OLLAMA_URL` | `http://ollama:11434` | LLM endpoint |
| `OLLAMA_MODEL` | `llama3.2:3b-instruct-q4_K_M` | Модель |
| `OLLAMA_TIMEOUT_SEC` | `30` | Таймаут запроса к LLM |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Модель эмбеддингов |
| `SIMILARITY_THRESHOLD` | `0.7` | Порог косинусного сходства |
| `MAX_MATCHES_PER_JOB` | `50` | Размер ANN candidate pool до rerank |
| `RERANK_THRESHOLD` | `0.55` | Мин. cross-encoder score для downstream scoring |
| `RERANK_TOP_K` | `10` | Сколько кандидатов оставить после cross-encoder rerank |
| `AI_QUEUE` | `ai-process` | Входная очередь |
| `AC_BATCH_QUEUE` | `ac-batch` | Очередь финального batch scoring |
| `AC_BATCH_INTERVAL_SEC` | `300` | Окно накопления батча |
| `AC_BATCH_MIN_JOBS` | `1` | Мин. заданий в батче |
| `AC_BATCH_MAX_JOBS` | `20` | Макс. заданий в батче |

### Telegram Bot

| Переменная | Default | Описание |
|-----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | — | ✓ |
| `API_URL` | `http://localhost:8080` | ✓ |
| `API_AUTH_TOKEN` | — | ✓ |
| `API_USER_HMAC_SECRET` | — | ✓ |
| `USER_ID_CACHE_TTL_SEC` | `300` | TTL кэша telegram_id→user_id |
| `HEARTBEAT_FILE` | `/tmp/telegram-bot-heartbeat` | Файл healthcheck |

---

## 9. Запуск и деплой

### Быстрый старт (dev)

```bash
cp .env.example .env
# Заполнить DATABASE_URL, REDIS_URL, TELEGRAM_BOT_TOKEN, API_AUTH_TOKEN, API_USER_HMAC_SECRET

make start           # Все сервисы кроме локального telegram-bot
make start-all       # Если нужен локальный telegram-bot
make doctor          # Health-check всего локального стека
make migrate         # Применить все миграции
make logs            # Посмотреть логи
make logs SERVICE=backend-api   # Логи конкретного сервиса
```

### Makefile-цели

| Команда | Описание |
|---------|----------|
| `make start` | Канонический локальный старт без telegram-bot |
| `make start-all` | Канонический локальный старт вместе с telegram-bot |
| `make stop` | Каноническая остановка локального стека |
| `make status` | Канонический статус контейнеров |
| `make doctor` | Канонический health-check локального стека |
| `make queues` | Канонический просмотр Redis-очередей |
| `make up` | Совместимый alias для `make start-all` |
| `make up-dev` | Совместимый alias для `make start` |
| `make down` | Базовая остановка локального стека |
| `make restart` | Перезапуск core/workers/monitoring без telegram-bot |
| `make logs` | Логи (можно `SERVICE=xxx`) |
| `make migrate` | Применить миграции |
| `make test` | Основные unit/config проверки: backend, ai-service, browser-service, telegram-bot, monitoring |
| `make test-all` | Полный локальный gate-run: workflow lint + unit + browser-service + coverage + monitoring + security |
| `make test-go` | Go unit tests |
| `make test-ai` | Python pytest |
| `make test-browser` | Python pytest для browser-service |
| `make test-bot` | Python pytest для telegram-bot |
| `make test-monitoring` | Проверка Prometheus/Alertmanager/compose конфигов |
| `make test-fast` | Быстрый набор: backend + ai-service без coverage |
| `make coverage` | Coverage gate: backend/internal + ai-service + telegram-bot |
| `make shell-api` | bash внутри backend-api |
| `make shell-db` | psql |
| `make shell-redis` | redis-cli |
| `make smoke-e2e` | Полный локальный E2E smoke |
| `make backup` | Дамп БД |
| `make restore` | Восстановить из дампа |
| `make backup-smoke` | Smoke backup/restore для локальной compose-БД |
| `make queue-status` | Подробный статус очередей, включая `user-rematch` и DLQ |
| `make dlq-status` | Только DLQ-очереди |
| `make up-monitoring` | Поднять monitoring-стек |
| `make mon-up` | Совместимый alias для `make up-monitoring` |
| `make clean-all` | Остановить + удалить тома + образы |
| `make help` | Показать полный список доступных команд |

`rollback` и DLQ reprocess не оформлены отдельными make-целями: используйте runbook из `docs/operations.md` и прямые команды `redis-cli`/restore-скрипты.

### Production (Railway)

Сервисы деплоятся через `railpack.json`. База данных и Redis — внешние управляемые сервисы.

**Порядок деплоя:**
1. Запустить `backend-migrate` (применит новые миграции)
2. Задеплоить `backend-api`
3. Задеплоить `backend-notifier` (с `DIGEST_CRON=0 * * * *`)
4. Задеплоить `backend-crawler`
5. Задеплоить AI-сервисы (нужен Ollama)
6. Задеплоить `telegram-bot`

**Обязательные env vars в Railway:**
- `DATABASE_URL` (с `sslmode=require`)
- `REDIS_URL` (с `rediss://` + password)
- `TELEGRAM_BOT_TOKEN`
- `API_AUTH_TOKEN` (≥32 chars, случайные)
- `API_USER_HMAC_SECRET` (≥32 chars, случайные)
- `APP_ENV=production`

### Активация Pro для пользователя

Платёжный шлюз не интегрирован — Pro устанавливается вручную через БД:

```sql
UPDATE users SET is_pro = TRUE WHERE telegram_id = 123456789;
```

---

## 10. Мониторинг

### Prometheus метрики

Каждый Go-сервис отдаёт `/metrics`:

| Метрика | Описание |
|---------|----------|
| `crawler_jobs_scraped_total` | Парсинг: кол-во найденных проектов |
| `crawler_run_duration_seconds` | Длительность одного запуска |
| `crawler_queue_depth` | Глубина очереди ai-process |
| `notifier_notifications_sent_total` | Отправленных уведомлений |
| `notifier_notifications_failed_total` | Неудачных отправок |
| `notifier_queue_depth{queue}` | Глубина match-notify / processing / dlq |
| `go_goroutines`, `go_memstats_*` | Runtime метрики |

### Healthchecks

| Сервис | Liveness | Readiness |
|--------|----------|-----------|
| backend-api | `/healthz` | `/readyz` (DB + Redis ping) |
| backend-notifier | `/healthz` | `/readyz` (DB + Redis ping) |
| backend-crawler | `/healthz` | `/readyz` |
| telegram-bot | heartbeat file age < 90 s | — |

### Alerting

Настроены через `monitoring/prometheus/alerts.yml`:
- High CPU / Memory / Disk
- DB / Redis unavailable
- Queue depth threshold exceeded
- Notification failure rate spike

---

## 11. Бизнес-логика подробно

### Как пользователь начинает получать уведомления

1. Пишет `/start` → бот регистрирует в БД
2. Отправляет `/profile Я Python-разработчик, специализируюсь на Django...` → профиль сохраняется, генерируется эмбеддинг
3. Crawler находит новые проекты на Kwork
4. AI-сервис сравнивает эмбеддинг проекта с эмбеддингом профиля
5. Cross-encoder reranker отсеивает слабые кандидаты и оставляет top-K
6. Финальный scorer считает top-5 и Actor генерирует объяснение
7. Notifier отправляет в Telegram с кнопками 👍/👎

### Дедупликация уведомлений

`UNIQUE(user_id, job_id)` в таблице `notifications`:
- Проект никогда не отправится одному пользователю дважды
- `EnsurePending`: INSERT ... ON CONFLICT DO NOTHING → wasInserted=false, shouldSend=false → пропустить

### Rate limiting (Free пользователи)

```
SentRecently(5 мин) → да → пропустить (удалить pending)
CountToday ≥ 5      → да → пропустить (удалить pending)
```

Pending-запись удаляется, чтобы не накапливать «просроченные» уведомления.

### Pro дайджест

```
Каждый час (cron):
  MSK_hour = now().In("Europe/Moscow").Hour()
  users = SELECT id FROM users WHERE is_pro AND notify_hour = MSK_hour

  для каждого user:
    already_sent = CountToday(user)
    remaining = maxPerDay - already_sent
    pending = GetPendingForUser(user)  -- ORDER BY final_score DESC
    pending = pending[:remaining]       -- обрезать по лимиту
    jobs = GetByIDs(pending.job_ids)
    Telegram.Send(batch)
    MarkSent(pending.job_ids)
```

Ключевое: для Pro-пользователей `EnsurePending` сохраняет запись, но `sendNotif.Execute` делает early return не отправляя немедленно — всё накапливается до нужного часа.

### Фидбек и обучение

```
User: 👍 на проект → feedback='good'
User: 👎 на проект → feedback='bad'

FeedbackSignal (за 30 дней, по навыкам):
  good_ratio = count(good) / total
  bad_ratio  = count(bad) / total
  net        = good_ratio - bad_ratio

adjust_candidates:
  for candidate in candidates:
    job_skills = candidate.skills ∩ known_skills
    skill_signal = avg(FeedbackSignal for job_skills)
    global_signal = FeedbackSignal(all jobs)
    blended = 0.5 * skill_signal + 0.5 * global_signal
    candidate.score *= (1 + blended.net * weight)
```

Результат: проекты с навыками, на которые пользователь реагировал 👍, получают бонус к скору. Проекты с «плохими» навыками — штраф.

### Final Scoring алгоритм

1. `pgvector ANN` выбирает top-N кандидатов по cosine similarity.
2. `cross-encoder rerank` считает `rerank_score` для пар `(profile_text, job_text)`.
3. Оставляются только кандидаты с `rerank_score >= RERANK_THRESHOLD`.
4. Для каждого кандидата считается мультипликативный `final_score`:
   `rerank_score * (1 + feedback_bonus) * preference_multiplier * time_decay_multiplier * competition_multiplier`.
5. Берутся top-5 по `final_score`.
6. Actor LLM одним batched-запросом возвращает `why_it_fits` для выбранных проектов.

### Изоляция данных (RLS)

```sql
-- При любой транзакции, изменяющей данные пользователя:
SET LOCAL app.current_user_id = {user_id};

-- RLS policy на таблицах:
CREATE POLICY user_isolation ON notifications
  USING (user_id = current_setting('app.current_user_id')::bigint);
```

Даже если в API-запросе подменить `{id}` в URL — RLS не позволит изменить данные чужого пользователя.
