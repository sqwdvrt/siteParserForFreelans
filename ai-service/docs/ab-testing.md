# A/B Testing Guide

Feature flags и A/B тестирование для безопасных экспериментов с ML моделями.

## Обзор

Система позволяет:
- Плавно переключать трафик между старой и новой моделью
- Сравнивать метрики по вариантам
- Быстро откатываться при деградации

## Feature Flags

### Конфигурация

```bash
# В .env или docker-compose:
FEATURE_FLAGS_JSON='{
  "ltr_ranker": {
    "enabled": true,
    "rollout_percentage": 50,
    "variants": {"old": 50, "new": 50}
  },
  "reranker_v2": {
    "enabled": true,
    "rollout_percentage": 10,
    "variants": {"old": 90, "new": 10}
  }
}'
```

### Использование в коде

```python
from ai_service.util.feature_flags import get_flags

flags = get_flags()

# Проверка включён ли флаг
if flags.is_enabled("ltr_ranker", user_id):
    variant = flags.get_variant("ltr_ranker", user_id)
    if variant == "new":
        score = ltr_ranker.predict_score(features)
    else:
        score = manual_formula(features)
```

### Детерминизм

Один и тот же `user_id` всегда получает один и тот же вариант.
Это гарантирует консистентность A/B теста.

## A/B Метрики

### Отслеживание

```python
from ai_service.util.ab_metrics import get_ab_metrics

metrics = get_ab_metrics()

# При обработке запроса
metrics.record_request(variant)
metrics.record_success(variant)  # или record_failure(variant)
metrics.record_feedback(variant, is_good=True)
metrics.record_latency(variant, seconds=0.15)
```

### Просмотр статистики

```python
stats = metrics.get_all_stats()
# {
#   "old": {
#     "requests": 1500,
#     "success_rate": 0.95,
#     "feedback_positive_rate": 0.72,
#     "avg_latency_seconds": 0.12
#   },
#   "new": {
#     "requests": 1500,
#     "success_rate": 0.97,
#     "feedback_positive_rate": 0.78,
#     "avg_latency_seconds": 0.08
#   }
# }
```

### Prometheus метрики

- `ai_ab_test_success_rate{variant="old|new"}`
- `ai_ab_test_feedback_positive_rate{variant="old|new"}`
- `ai_ab_test_avg_latency_seconds{variant="old|new"}`

## A/B Test Workflow

### 1. Canary (5%)

```json
{
  "ltr_ranker": {
    "enabled": true,
    "rollout_percentage": 5,
    "variants": {"old": 95, "new": 5}
  }
}
```

Запускаем 5% трафика на новую модель. Мониторим 24-48 часов.

### 2. Ramp Up (25%)

Если метрики новые >= старых:

```json
{
  "ltr_ranker": {
    "variants": {"old": 75, "new": 25}
  }
}
```

### 3. Full Rollout (50%)

```json
{
  "ltr_ranker": {
    "variants": {"old": 50, "new": 50}
  }
}
```

### 4. Complete (100%)

```json
{
  "ltr_ranker": {
    "variants": {"old": 0, "new": 100}
  }
}
```

После подтверждения — убираем флаг, новая модель становится default.

## Rollback

При деградации метрик:

```json
{
  "ltr_ranker": {
    "enabled": false
  }
}
```

Мгновенный откат — весь трафик идёт на старую модель.

## Критерии успеха

| Метрика | Критерий |
|---------|----------|
| Feedback positive rate | new > old на ≥ 5% |
| Success rate | new >= old |
| Avg latency | new <= old × 1.2 |
| Error rate | new <= old |

## LTR Ranker

### Обучение

```bash
cd ai-service
python scripts/train_ltr_model.py --output models/ltr/
```

### Деплой

```bash
# В .env:
LTR_MODEL_PATH=models/ltr/model.txt

# Feature flag:
FEATURE_FLAGS_JSON='{"ltr_ranker": {"enabled": true, "variants": {"old": 50, "new": 50}}}'
```

### Fallback

Если LTR модель недоступна или падает — автоматически используется ручная формула.

## Proxy Pool (Backend)

### Конфигурация

```bash
# В .env backend:
PROXY_URLS=http://proxy1:8080,http://proxy2:8080,http://proxy3:8080
PROXY_STRATEGY=success_rate
PROXY_BAN_DURATION=30m
PROXY_HEALTH_CHECK_INTERVAL=60s
```

### Стратегии ротации

| Стратегия | Описание |
|-----------|----------|
| `round_robin` | По порядку |
| `random` | Случайный выбор |
| `least_used` | Самый давно не использованный |
| `success_rate` | С лучшим success rate |

### Метрики

```go
stats := pool.Stats()
// map[string]any{
//   "total": 3,
//   "available": 2,
//   "banned": 1,
//   "successes": 150,
//   "failures": 12,
//   "ban_rate": 0.33,
//   "strategy": "success_rate",
// }
```

### HTTP Transport

```go
pool, _ := proxy.NewPool(cfg)
client := &http.Client{
    Transport: pool.HTTPTransport(nil),
}
resp, err := client.Get("https://example.com")
// Автоматически выбирает прокси из пула
// RecordSuccess/RecordFailure при каждом запросе
```
