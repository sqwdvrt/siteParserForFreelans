# Тестирование и код-ревью

Руководство по тестам и проверке кода после каждого этапа.

---

## Целевое покрытие: ≥ 70%

**Проверка:**
```bash
# Go
cd backend && go test ./... -cover
# Ожидание: coverage: XX.X% of statements, где XX ≥ 70

# Python
cd ai-service && pytest --cov=ai_service --cov-fail-under=70
```

---

## Инструменты

### Go
| Инструмент | Назначение | Команда |
|------------|------------|---------|
| go test | Юнит и интеграционные тесты | `go test ./... -cover -race` |
| go vet | Статический анализ | `go vet ./...` |
| staticcheck | Доп. линтер | `staticcheck ./...` |

### Python
| Инструмент | Назначение | Команда |
|------------|------------|---------|
| pytest | Тесты | `pytest tests/ -v` |
| pytest-cov | Покрытие | `pytest --cov=ai_service` |
| bandit | Безопасность | `bandit -r ai_service/` |
| ruff | Линтер | `ruff check ai_service/` |

---

## Чеклист код-ревью по категориям

### Безопасность
- [ ] Нет SQL-инъекций (только параметризованные запросы)
- [ ] Нет SSRF (валидация URL, запрет localhost/внутренних IP)
- [ ] Секреты не в коде и не в логах
- [ ] Валидация и санитизация пользовательского ввода
- [ ] Экранирование при выводе (HTML, Markdown в Telegram)

### Надёжность
- [ ] Обработка всех ошибок (нет необработанных panic/exception)
- [ ] Таймауты на внешние вызовы (HTTP, DB, Redis)
- [ ] Graceful shutdown (горутины, соединения)
- [ ] Лимиты на размер данных (DoS-защита)

### Качество кода
- [ ] Нет дублирования
- [ ] Понятные имена и структура
- [ ] Логирование на нужном уровне

---

## Типичные уязвимости по компонентам

### Crawler (Go)
- **SSRF:** URL может вести на внутренний сервис → проверять host
- **DoS:** Огромный HTML → лимит на размер ответа
- **Паника в extractor:** Невалидный HTML → recover + логирование

### Repository (Go/Python)
- **SQL-инъекция:** Конкатенация строк в запросы → только `$1`, `?`, `%s` с параметрами
- **Connection leak:** Не закрытые соединения при ошибках

### AI Service (Python)
- **Промпт-инъекция:** Пользовательский текст в промпт → санитизация
- **Память:** Большие тексты без лимита → обрезка до N символов

### Telegram
- **Инъекция в разметку:** Текст проекта в сообщении → экранирование `_`, `*`, `[`, `]`
- **Спам:** Rate limit и лимит в день

---

## Структура тестов

Соответствует **ARCHITECTURE.md**: тесты рядом с кодом или в `*_test.go` / `tests/`.

### Go (backend)
```
backend/
├── internal/
│   ├── adapter/
│   │   ├── http/
│   │   │   ├── fetcher.go
│   │   │   └── fetcher_test.go
│   │   ├── kwork/
│   │   │   ├── extractor.go
│   │   │   └── extractor_test.go
│   │   └── postgres/
│   │       ├── job_repository.go
│   │       └── job_repository_test.go
│   └── usecase/
│       ├── crawl_projects.go
│       └── crawl_projects_test.go   # с моками port.*
└── testdata/
    └── kwork_list.html
```

### Python (ai-service)
```
ai-service/
├── src/ai_service/
│   ├── adapter/
│   │   ├── sentence_transformers/
│   │   │   └── embedding_service.py
│   │   └── ...
│   └── usecase/
│       └── process_job.py
├── tests/
│   ├── conftest.py           # Фикстуры, моки
│   ├── test_text_cleaner.py
│   ├── test_embedding_service.py
│   └── test_process_job_usecase.py
└── fixtures/
    └── sample_job.json
```

---

## CI (рекомендация)

```yaml
# Пример для GitHub Actions
- name: Go tests
  run: go test ./... -cover -coverprofile=coverage.out
- name: Go coverage
  run: |
    coverage=$(go tool cover -func=coverage.out | grep total | awk '{print $3}' | sed 's/%//')
    [ $(echo "$coverage >= 70" | bc) -eq 1 ]

- name: Python tests
  run: pytest --cov=ai_service --cov-fail-under=70
- name: Bandit
  run: bandit -r ai_service/ -ll
```
