# Site Parser for Freelance

Автоматическое обнаружение новых фриланс-проектов, их анализ и отправка пользователю только релевантных заказов.

**Сервис только:** находит → анализирует → ранжирует → уведомляет.

**Это не:** биржа фриланса, маркетплейс, автоотклик, инструмент подачи заявок.

**KPI:** ≤ 5 уведомлений в день, большинство релевантны. Оптимизировать релевантность, а не количество источников.

**Не:** агрегатор вакансий, массовая рассылка, парсинг без задержек, обход логина, автоотклики.

**Matching:** embedding similarity (cosine), не keyword filtering.

**Архитектура:** Core (Go) — парсинг, API, очереди, уведомления. AI Service (Python) — embeddings, matching. Crawler не вызывает LLM.

## Документация

Вся документация проекта находится в папке **`rules/`**:

| Файл | Описание |
|------|----------|
| [rules/ARCHITECTURE.md](rules/ARCHITECTURE.md) | Clean Architecture, структура проекта |
| [rules/DECISIONS.md](rules/DECISIONS.md) | Принятые решения (Kwork, Redis, embeddings) |
| [rules/ROADMAP.md](rules/ROADMAP.md) | План разработки по этапам |
| [rules/TESTING.md](rules/TESTING.md) | Тестирование и код-ревью |

## Быстрый старт

```bash
# 1. Запустить PostgreSQL + Redis
docker compose up -d

# 2. Проверить миграции (опционально)
./scripts/verify_migrations.sh
```

Миграции применяются автоматически при первом запуске PostgreSQL (volume пустой).

См. [rules/ROADMAP.md](rules/ROADMAP.md) для полного плана разработки.
