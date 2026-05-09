# Fine-Tuning Embedding Models

Контрастивное обучение SentenceTransformer модели для улучшения качества эмбеддингов.

## Обзор

Fine-tuning использует **TripletLoss** для обучения модели различать релевантные и нерелевантные job для каждого пользователя.

### Принцип работы

```
User (anchor) ──┐
                ├──> Positive Job (good feedback) ──> minimize distance
                └──> Negative Job (bad feedback)   ──> maximize distance
```

Модель учится:
- Приближать эмбеддинги jobs с **positive** feedback к эмбеддингу пользователя
- Отдалять эмбеддинги jobs с **negative** feedback

### Pipeline

```
1. Export Data    →  Триплеты (user, good_job, bad_job) из PostgreSQL
2. Build Dataset  →  ContrastiveDataset с фильтрацией и mining
3. Fine-Tune      →  TripletLoss обучение (3-5 epochs)
4. Evaluate       →  Сравнение base vs fine-tuned модели
5. Deploy         →  Копирование модели в production
```

## Быстрый старт

### 1. Экспорт training данных

```bash
cd ai-service

# Экспорт последних 30 дней feedback
python scripts/export_training_data.py \
    --days 30 \
    --output training_data/ \
    --dsn "postgresql://user:pass@postgres:5432/site_parser"

# Результат:
# training_data/triplets_20260406.json
# training_data/eval_pairs_20260406.json
```

### 2. Fine-Tuning

```bash
# Базовое обучение
python scripts/fine_tune_embedding_model.py \
    --triplets-file training_data/triplets_20260406.json \
    --epochs 3 \
    --batch-size 16 \
    --evaluate

# С кастомной моделью
python scripts/fine_tune_embedding_model.py \
    --triplets-file training_data/triplets_20260406.json \
    --base-model sentence-transformers/all-mpnet-base-v2 \
    --epochs 5 \
    --batch-size 32 \
    --learning-rate 1e-5 \
    --evaluate

# Dry-run (проверка данных без обучения)
python scripts/fine_tune_embedding_model.py \
    --triplets-file training_data/triplets_20260406.json \
    --dry-run
```

### 3. Оценка результатов

После обучения скрипт выведет:

```
Evaluation Results:
  Base model accuracy:      0.68
  Fine-tuned model accuracy: 0.82
  Improvement:              +14.0%
```

**Accuracy** = доля триплетов, где `cosine(user, good_job) > cosine(user, bad_job)`.

### 4. Деплой новой модели

```bash
# Модель сохранена в:
# models/fine-tuned/all-MiniLM-L6-v2_20260406_143022/

# Обновите docker-compose.yml или env:
EMBEDDING_MODEL=models/fine-tuned/all-MiniLM-L6-v2_20260406_143022

# Перезапустите ai-service
docker-compose restart ai-service
```

## Аргументы скриптов

### export_training_data.py

| Аргумент | По умолчанию | Описание |
|----------|--------------|----------|
| `--days` | 30 | Временное окно feedback (дней) |
| `--output` | training_data | Директория вывода |
| `--min-feedback` | 1 | Мин. feedback на пользователя |
| `--max-triplets-per-user` | 10 | Макс. триплетов на пользователя |
| `--dsn` | DATABASE_URL | PostgreSQL connection string |

### fine_tune_embedding_model.py

| Аргумент | По умолчанию | Описание |
|----------|--------------|----------|
| `--triplets-file` | (required) | Файл с триплетами |
| `--base-model` | all-MiniLM-L6-v2 | Базовая модель |
| `--epochs` | 3 | Количество эпох |
| `--batch-size` | 16 | Размер батча |
| `--learning-rate` | 2e-5 | Learning rate |
| `--output-dir` | models/fine-tuned/ | Директория моделей |
| `--evaluate` | false | Запустить оценку |
| `--dry-run` | false | Проверка данных |

## Mining стратегии

Модуль `contrastive_dataset.py` поддерживает три стратегии:

### 1. Random Negative Mining
Случайная выборка негативов. Простая, но эффективная baseline стратегия.

### 2. Hard Negative Mining
Выбирает jobs с **высокой cosine similarity**, но bad feedback. Модель учится различать "похожие, но неподходящие" jobs.

### 3. Semi-Hard Negative Mining
Выбирает negatives которые ближе к anchor чем positives, но не слишком близко. Баланс сложности — основа для стабильного обучения.

## Метрики качества

### Triplet Accuracy
Доля триплетов где `cosine(anchor, positive) > cosine(anchor, negative)`.

- **< 0.60**: Модель не научилась различать
- **0.60-0.75**: Базовое качество
- **0.75-0.85**: Хорошее качество
- **> 0.85**: Отличное качество

### Рекомендации

- Минимум **500 триплетов** для значимого обучения
- Оптимально **2000-5000 триплетов**
- Соотношение good/bad должно быть **0.3-0.7**

## A/B Тестирование

Для безопасного тестирования новой модели:

1. Загрузите fine-tuned модель с новым именем:
   ```
   EMBEDDING_MODEL_V2=models/fine-tuned/all-MiniLM-L6-v2_20260406
   ```

2. Реализуйте роутинг (50% трафика на каждую модель)

3. Сравните метрики:
   - Feedback positive ratio
   - Notification click-through rate
   - User retention

4. При улучшении — переключите весь трафик на новую модель

## Troubleshooting

### "No triplets exported"
- Проверьте наличие feedback данных: `SELECT COUNT(*) FROM user_feedback;`
- Увеличьте временное окно: `--days 90`

### "CUDA out of memory"
- Уменьшите batch size: `--batch-size 8`
- Используйте CPU (медленнее, но стабильнее)

### "Model accuracy decreased"
- Возможно переобучение — уменьшите epochs
- Проверьте качество данных (дубликаты, шум)
- Попробуйте другую mining стратегию

## Ссылки

- [SentenceTransformer TripletLoss](https://sbert.net/docs/package_reference/sentence_transformer/losses.html#tripletloss)
- [Contrastive Learning Guide](https://sbert.net/docs/training/overview.html)
- [Mining Strategies](https://arxiv.org/abs/1703.07737)
