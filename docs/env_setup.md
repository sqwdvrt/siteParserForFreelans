# Откуда взять переменные окружения

| Переменная | Где взять |
|------------|-----------|
| **TELEGRAM_BOT_TOKEN** | [@BotFather](https://t.me/BotFather) → /newbot → скопировать токен |
| **DATABASE_URL** | PostgreSQL: `postgres://user:password@host:5432/dbname?sslmode=disable`. Локально: `docker-compose up -d postgres` → см. `.env.example` |
| **REDIS_URL** | Redis: `redis://localhost:6379/0`. Локально: `docker-compose up -d redis` |
| **API_URL** | URL Backend API. Локально: `http://localhost:8080` |
| **API_ADDR** | Порт, на котором слушает API. По умолчанию: `:8080` |

**Важно:** `.env` в `.gitignore` — секреты не попадут в репозиторий.

### Запуск (из корня проекта)

**Перед первым запуском AI Service:**
```bash
pip3 install python-dotenv redis psycopg2-binary pgvector sentence-transformers
```

**Telegram-бот:** `pip3 install python-dotenv`

```bash
# API
cd backend && go run ./cmd/api

# AI user-embed consumer
cd ai-service && PYTHONPATH=src python3 cmd/user_embed_consumer/main.py

# Telegram-бот
cd telegram-bot && python3 main.py
```
