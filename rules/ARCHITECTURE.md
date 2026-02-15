# Архитектура проекта

Чистая архитектура (Clean Architecture) с чётким разделением слоёв и зависимостей.

---

## Принципы

1. **Зависимости направлены внутрь** — Domain не зависит ни от чего
2. **Инверсия зависимостей** — бизнес-логика зависит от интерфейсов (портов), а не от реализаций
3. **Изоляция фреймворков** — инфраструктура заменяема без изменения ядра
4. **Тестируемость** — каждый слой можно тестировать с моками

---

## Два независимых сервиса

| Core (Go) | AI Service (Python) |
|-----------|---------------------|
| Парсинг сайтов | Обработка текста |
| Хранение | Классификация |
| API | Embeddings |
| Очереди | Matching |
| Уведомления | |

**❗ Запрещено смешивать AI-логику с crawler. Crawler не должен вызывать LLM.**

Связь только через очередь: Core сохраняет job → кладёт job_id в Redis → AI Service забирает и обрабатывает.

---

## Telegram Bot — тонкий клиент

**Бот — только клиент API.**

| Не делает | Делает |
|-----------|--------|
| Не содержит бизнес-логики | API request → показывает результат |
| Не хранит данные | |
| Не выполняет matching | |

**Поток:** `Пользователь → Бот → Backend API → Бот → Пользователь`

Регистрация, профиль, matching, хранение — в Backend. Бот только пересылает запросы и отображает ответы.

**Уведомления** (push): Backend (Notifier) → Telegram API → Пользователь. Бот не участвует.

---

## Слои

```
┌─────────────────────────────────────────────────────────────┐
│  Drivers (HTTP, CLI, Cron, Telegram API)                     │  ← Внешние триггеры
├─────────────────────────────────────────────────────────────┤
│  Adapters (PostgreSQL, Redis, Kwork Fetcher, Extractor)      │  ← Реализации портов
├─────────────────────────────────────────────────────────────┤
│  Ports (интерфейсы: Repository, Fetcher, Queue, Notifier)   │  ← Контракты
├─────────────────────────────────────────────────────────────┤
│  Use Cases (CrawlProjects, ProcessJob, MatchAndNotify)        │  ← Оркестрация
├─────────────────────────────────────────────────────────────┤
│  Domain (Job, User, Notification — сущности и правила)        │  ← Ядро
└─────────────────────────────────────────────────────────────┘
```

---

## Структура проекта

```
siteParserForFreelans/
├── backend/                          # Go — Core service
│   ├── cmd/
│   │   └── crawler/
│   │       └── main.go                # Точка входа
│   ├── internal/
│   │   ├── domain/                   # Ядро — без зависимостей
│   │   │   ├── job.go
│   │   │   ├── user.go
│   │   │   └── notification.go
│   │   ├── usecase/                  # Сценарии
│   │   │   ├── crawl_projects.go
│   │   │   ├── enqueue_job.go
│   │   │   └── register_user.go
│   │   ├── port/                     # Интерфейсы (контракты)
│   │   │   ├── repository.go
│   │   │   ├── fetcher.go
│   │   │   ├── extractor.go
│   │   │   ├── queue.go
│   │   │   └── notifier.go
│   │   └── adapter/                  # Реализации портов
│   │       ├── postgres/
│   │       │   ├── job_repository.go
│   │       │   └── user_repository.go
│   │       ├── redis/
│   │       │   └── queue.go
│   │       ├── http/
│   │       │   └── fetcher.go
│   │       ├── kwork/
│   │       │   └── extractor.go
│   │       └── telegram/
│   │           └── notifier.go          # Отправка уведомлений (Backend → Telegram API)
│   │
│   └── api/                              # HTTP API для бота и др.
│       └── handler/
│           ├── user.go                   # POST /users, PUT /profile
│           └── ...
│
├── telegram-bot/                         # Тонкий клиент (отдельный сервис)
│   └── main.go                           # getUpdates → API call → sendMessage
│   ├── pkg/                          # Переиспользуемые утилиты (опционально)
│   │   ├── ratelimit/
│   │   └── logger/
│   ├── migrations/
│   ├── go.mod
│   └── go.sum
│
├── ai-service/                       # Python — AI service
│   ├── src/
│   │   └── ai_service/
│   │       ├── domain/               # Сущности (dataclasses)
│   │       │   ├── job.py
│   │       │   └── embedding.py
│   │       ├── usecase/              # Сценарии
│   │       │   ├── process_job.py
│   │       │   ├── classify_job.py
│   │       │   └── match_users.py
│   │       ├── port/                 # Абстрактные классы / Protocol
│   │       │   ├── repository.py
│   │       │   ├── embedding_service.py
│   │       │   └── classifier.py
│   │       └── adapter/              # Реализации
│   │           ├── postgres/
│   │           │   └── job_repository.py
│   │           ├── redis/
│   │           │   └── queue_consumer.py
│   │           ├── sentence_transformers/
│   │           │   └── embedding_service.py
│   │           └── llm/
│   │               └── classifier.py
│   ├── tests/
│   ├── requirements.txt
│   └── pyproject.toml
│
├── shared/                           # Общие контракты (опционально)
│   └── schema/                       # JSON-схемы для очереди
│       └── ai_process_payload.json
│
├── rules/
│   ├── ARCHITECTURE.md
│   ├── DECISIONS.md
│   ├── ROADMAP.md
│   └── TESTING.md
├── docker-compose.yml
└── .env.example
```

---

## Backend (Go) — детализация

### Domain

```go
// internal/domain/job.go
package domain

type Job struct {
    ID         int64
    Source     string
    URL        string
    ExternalID string
    Title      string
    Description string
    Budget     string
    Skills     []string
    PostedAt   *time.Time
    RawHTML    string
    CreatedAt  time.Time
}

// Валидация — в domain
func (j *Job) Validate() error { ... }
```

**Правило:** Только структуры и чистые функции. Нет импортов из `adapter`, `pkg` (кроме стандартной библиотеки).

---

### Ports (интерфейсы)

```go
// internal/port/repository.go
package port

type JobRepository interface {
    Save(ctx context.Context, job *domain.Job) (int64, error)
    GetByURL(ctx context.Context, url string) (*domain.Job, error)
    ExistsByURL(ctx context.Context, url string) (bool, error)
}

// internal/port/fetcher.go
type Fetcher interface {
    Fetch(ctx context.Context, url string) ([]byte, error)
}

// internal/port/extractor.go
type Extractor interface {
    ExtractList(html []byte) ([]string, error)
    ExtractDetail(html []byte, url string) (*domain.Job, error)
}

// internal/port/queue.go
type JobQueue interface {
    Enqueue(ctx context.Context, jobID int64) error
}

// internal/port/notifier.go
type Notifier interface {
    Send(ctx context.Context, userID int64, job *domain.Job, score float64) error
}
```

**Правило:** Интерфейсы объявлены в `port`, используют типы из `domain`.

---

### Use Cases

```go
// internal/usecase/crawl_projects.go
package usecase

type CrawlProjects struct {
    fetcher   port.Fetcher
    extractor port.Extractor
    repo      port.JobRepository
    queue     port.JobQueue
}

func (u *CrawlProjects) Execute(ctx context.Context) (saved int, err error) {
    html, err := u.fetcher.Fetch(ctx, listURL)
    if err != nil { return 0, err }
    
    urls, err := u.extractor.ExtractList(html)
    if err != nil { return 0, err }
    
    for _, url := range urls {
        if exists, _ := u.repo.ExistsByURL(ctx, url); exists { continue }
        detailHTML, _ := u.fetcher.Fetch(ctx, url)
        job, _ := u.extractor.ExtractDetail(detailHTML, url)
        id, _ := u.repo.Save(ctx, job)
        _ = u.queue.Enqueue(ctx, id)
        saved++
    }
    return saved, nil
}
```

**Правило:** Use case зависит только от `port` и `domain`. Внедрение зависимостей через конструктор (DI).

---

### Adapters

```go
// internal/adapter/kwork/extractor.go
package kwork

type Extractor struct{}

func (e *Extractor) ExtractList(html []byte) ([]string, error) {
    // Парсинг HTML Kwork
}

func (e *Extractor) ExtractDetail(html []byte, url string) (*domain.Job, error) {
    // ...
}
```

**Правило:** Adapter реализует интерфейс из `port`. Импортирует `domain` и конкретные библиотеки (goquery, pgx, redis).

---

### Dependency Injection (main.go)

```go
// cmd/crawler/main.go
func main() {
    cfg := loadConfig()
    
    // Adapters
    repo := postgres.NewJobRepository(cfg.DB)
    fetcher := http.NewFetcher(cfg.RateLimit)
    extractor := kwork.NewExtractor()
    queue := redis.NewQueue(cfg.Redis, "ai-process")
    
    // Use case
    crawl := usecase.NewCrawlProjects(fetcher, extractor, repo, queue)
    
    // Driver: cron
    c := cron.New()
    c.AddFunc("*/5 * * * *", func() { crawl.Execute(context.Background()) })
    c.Start()
    // ...
}
```

**Правило:** Сборка в `main` или отдельном `wire`/`fx` модуле. Use case не создаёт адаптеры.

---

## AI Service (Python) — детализация

### Domain

```python
# src/ai_service/domain/job.py
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Job:
    id: int
    title: str
    description: str
    raw_html: str

@dataclass
class JobEmbedding:
    job_id: int
    embedding: list[float]
    metadata: dict
```

---

### Ports (Protocol / ABC)

```python
# src/ai_service/port/repository.py
from abc import ABC, abstractmethod

class JobRepository(ABC):
    @abstractmethod
    def get(self, job_id: int) -> Job | None: ...

    @abstractmethod
    def save_embedding(self, job_id: int, embedding: list[float], metadata: dict) -> None: ...

# src/ai_service/port/embedding_service.py
class EmbeddingService(ABC):
    @abstractmethod
    def encode(self, text: str) -> list[float]: ...
```

---

### Use Cases

```python
# src/ai_service/usecase/process_job.py
class ProcessJobUseCase:
    def __init__(
        self,
        repo: JobRepository,
        embedding_service: EmbeddingService,
        text_cleaner: TextCleaner,
    ):
        self.repo = repo
        self.embedding = embedding_service
        self.cleaner = text_cleaner

    def execute(self, job_id: int) -> None:
        job = self.repo.get(job_id)
        if not job:
            return
        text = self.cleaner.clean(job.raw_html)
        vector = self.embedding.encode(text)
        self.repo.save_embedding(job_id, vector, {"model": "..."})
```

---

### Adapters

```python
# src/ai_service/adapter/sentence_transformers/embedding_service.py
from sentence_transformers import SentenceTransformer

class SentenceTransformerEmbedding(EmbeddingService):
    def __init__(self, model_name: str):
        self.model = SentenceTransformer(model_name)

    def encode(self, text: str) -> list[float]:
        return self.model.encode(text).tolist()
```

---

## Правила зависимостей

| Слой    | Может импортировать      | Не может импортировать |
|---------|--------------------------|-------------------------|
| domain  | —                        | usecase, port, adapter |
| usecase | domain, port             | adapter                 |
| port    | domain                   | adapter                 |
| adapter | domain, port, библиотеки | usecase                 |
| cmd     | всё                      | —                       |

---

## Диаграмма потоков данных

```
                    Backend (Go)
┌──────────────────────────────────────────────────────────┐
│  Cron Driver                                             │
│       │                                                  │
│       ▼                                                  │
│  CrawlProjectsUseCase                                    │
│       │  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌────┐ │
│       ├──► Fetcher │  │ Extractor │  │ Repo    │  │Queue│ │
│       │  └────┬────┘  └────┬─────┘  └────┬────┘  └──┬──┘ │
│       │       │            │            │          │    │
│  Adapters: HTTP      Kwork        Postgres      Redis   │
└──────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
                    AI Service (Python)                Queue
┌──────────────────────────────────────────────────────────┐
│  Consumer (driver)                                        │
│       │                                                  │
│       ▼                                                  │
│  ProcessJobUseCase ──► MatchUsersUseCase                  │
│       │  ┌──────────┐  ┌────────────┐  ┌─────────────┐  │
│       ├──► Repo     │  │ Embedding  │  │ Classifier  │  │
│       │  └────┬─────┘  └─────┬──────┘  └──────┬──────┘  │
│  Adapters: Postgres   SentenceTransformer    LLM         │
└──────────────────────────────────────────────────────────┘
```

---

## Добавление нового источника (например, FL.ru)

1. Создать `internal/adapter/flru/extractor.go` — реализация `port.Extractor`
2. В `main.go` выбрать extractor по конфигу: `extractor := kwork.New()` или `extractor := flru.New()`
3. **Use case не меняется** — он работает с интерфейсом

---

## Добавление новой очереди (например, RabbitMQ)

1. Создать `internal/adapter/rabbitmq/queue.go` — реализация `port.JobQueue`
2. В `main.go`: `queue := rabbitmq.New(cfg)` вместо `redis.New()`
3. **Use case не меняется**

---

## Проверка архитектуры

```bash
# Go: запрет импортов usecase → adapter
cd backend && go list -m all  # зависимости

# Ручная проверка: в internal/usecase/ не должно быть импортов
# из internal/adapter/
```

Рекомендуется использовать [go-arch-lint](https://github.com/fdaines/arch-go) или аналог для автоматической проверки.
