# Troubleshooting Guide

Практические runbook'и для решения инцидентов в production среде.

## 1. OOM kill — AI сервис

**Симптомы:** контейнер `ai-runtime` или `ai-ac-consumer` перезапустился, в логах `Killed` или exit code 137.

**Диагностика:**
```bash
docker inspect ai-runtime | grep -A3 OOMKilled
dmesg | grep -i "oom\|killed" | tail -20
free -h
```

**Решение:**
1. Проверить swap: `swapon --show` — если нет, добавить 2GB (см. `docs/vps_deploy.md` §2).
2. Увеличить `AI_MEM_LIMIT` в `.env.production` (default: 2g).
   - Перезагрузить сервис: `docker compose ... restart ai-runtime`
3. Если Gemini вызов стабильно OOM-kill'ит — это upstream проблема:
   - Проверить логи: `docker logs ai-runtime | grep -i "memory\|timeout\|rss"`
   - Убедиться, что batch размер адекватен для памяти (`AI_BATCH_SIZE` в env)

---

## 2. Queue stall — задания не обрабатываются

**Симптомы:** Grafana → Queues dashboard показывает `ai-process` растёт, но DLQ пуст. AI consumer жив (нет перезапусков, нет ошибок в логах).

**Диагностика:**
```bash
# Проверить inflight задания
docker exec infra-redis-1 redis-cli --tls \
  --cacert /home/deploy/infra/certs/server.crt \
  -a "$REDIS_PASSWORD" \
  llen ai-process:processing

# Посмотреть какое-то stuck задание
docker exec infra-redis-1 redis-cli --tls \
  --cacert /home/deploy/infra/certs/server.crt \
  -a "$REDIS_PASSWORD" \
  lrange ai-process:processing 0 0 | python3 -m json.tool

# Проверить, что consumer жив
docker logs ai-runtime --tail 20
```

**Решение:**
1. Перезапустить AI consumer:
   ```bash
   docker compose -f /home/deploy/releases/current/docker-compose.prod.yml \
     -f /home/deploy/releases/current/docker-compose.ssl.yml \
     restart ai-runtime
   ```
   При старте срабатывает `recover_stuck()` / `reclaim_stuck()` — inflight задания переносятся обратно в очередь.

2. Если stuck job старше `_DEFAULT_RECLAIM_THRESHOLD_SEC` (300s) — он будет автоматически reclaim'нут даже без рестарта.

3. Проверить логи AI consumer на ошибки обработки:
   ```bash
   docker logs ai-runtime --since 5m | grep -i error
   ```

---

## 3. DLQ growth — задания в dead letter queue

**Симптомы:** `ai-process:dlq` растёт в Grafana или в redis-cli. Сообщения не обрабатываются и не удаляются.

**Диагностика:**
```bash
# Посмотреть размер DLQ
docker exec infra-redis-1 redis-cli --tls \
  --cacert /home/deploy/infra/certs/server.crt \
  -a "$REDIS_PASSWORD" \
  llen ai-process:dlq

# Посмотреть первые несколько задач
docker exec infra-redis-1 redis-cli --tls \
  --cacert /home/deploy/infra/certs/server.crt \
  -a "$REDIS_PASSWORD" \
  lrange ai-process:dlq 0 4 | python3 -m json.tool
```

Смотрим `_retry_count` и `_error_msg`. Ищем `job_id` в логах `ai-runtime`:
```bash
docker logs ai-runtime --since 1h | grep "job_id=<ID>"
```

**Решение:**

**Если transient DB error** (connection refused, timeout):
1. Исправить проблему БД (перезапустить Postgres, проверить диск, восстановить соединение).
2. Requeue все DLQ задания обратно в основную очередь:
   ```bash
   docker exec infra-redis-1 redis-cli --tls \
     --cacert /home/deploy/infra/certs/server.crt \
     -a "$REDIS_PASSWORD" \
     eval "local n=redis.call('llen','ai-process:dlq'); \
     for i=1,n do \
       local v=redis.call('rpop','ai-process:dlq'); \
       redis.call('lpush','ai-process',v) \
     end; \
     return n" 0
   ```

**Если постоянная ошибка** (bad request, validation error):
1. Изучить payload в DLQ и причину ошибки в логах.
2. Проверить целостность данных в БД:
   ```bash
   docker exec infra-postgres-1 psql -U site_parser -d site_parser \
     -c "SELECT id, title FROM jobs WHERE id = '<bad_job_id>';"
   ```
3. Исправить данные или удалить задание вручную:
   ```bash
   # Удалить одно задание
   docker exec infra-redis-1 redis-cli --tls \
     --cacert /home/deploy/infra/certs/server.crt \
     -a "$REDIS_PASSWORD" \
     lpop ai-process:dlq
   ```

---

## 4. Redis connection loss

**Симптомы:** backend возвращает 503 на `/readyz`, в логах `redis: connection refused` или TLS handshake error. Очереди не обновляются.

**Диагностика:**
```bash
# Проверить Redis контейнер живой
docker ps | grep infra-redis

# Посмотреть логи Redis
docker compose -f /home/deploy/infra/docker-compose.yml logs redis | tail -30

# Проверить TLS сертификат истёк ли
openssl s_client -connect 127.0.0.1:6379 -tls1_2 2>&1 | grep -A1 "Verify return\|Not After"
```

**Решение:**

1. **Перезапустить Redis:**
   ```bash
   cd /home/deploy/infra && docker compose up -d redis
   ```

2. **Проверить AOF:**
   ```bash
   docker exec infra-redis-1 redis-cli --tls \
     --cacert /home/deploy/infra/certs/server.crt \
     -a "$REDIS_PASSWORD" \
     INFO persistence | grep -E "aof_enabled|aof_rewrite"
   ```
   Если `aof_rewrite_in_progress: 1` — дождаться завершения (может занять минуты).

3. **Если certs истекли** (Verify return code: 18):
   ```bash
   cd /home/deploy/infra
   # Пересоздать самоподписанные сертификаты (см. docs/vps_deploy.md §4.1)
   openssl req -x509 -nodes -newkey rsa:2048 -keyout certs/redis.key \
     -out certs/server.crt -days 365 -subj "/CN=redis"
   docker compose up -d redis
   ```

4. **Проверить свободное место на диске:**
   ```bash
   df -h /home/deploy/infra
   ```
   Если < 10% свободно — чистить старые снимки или логи.

---

## 5. Telegram webhook failures

**Симптомы:** бот не отвечает на сообщения. Grafana показывает `telegram_webhook_errors_total` растёт или пользователи жалуются что бот молчит.

**Диагностика:**
```bash
# Посмотреть логи telegram-bot контейнера
docker logs siteparser-telegram-bot-1 --tail 50

# Проверить webhook зарегистрирован
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" | python3 -m json.tool

# Проверить, что бот контейнер жив
docker ps | grep telegram-bot
```

**Решение:**

**Если SSL certificate error** (`last_error_message: "SSL certificate"` в webhook info):
1. Обновить Let's Encrypt сертификат:
   ```bash
   sudo certbot renew --force-renewal
   sudo systemctl reload nginx
   ```
2. Переустановить webhook в Telegram:
   ```bash
   curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
     -F "url=https://<VPS_DOMAIN>/telegram" \
     -F "certificate=@/etc/letsencrypt/live/<VPS_DOMAIN>/fullchain.pem"
   ```

**Если бот упал**:
1. Перезапустить:
   ```bash
   docker compose -f /home/deploy/releases/current/docker-compose.prod.yml \
     -f /home/deploy/releases/current/docker-compose.ssl.yml \
     restart telegram-bot
   ```

**Если webhook inbox завис** (новые обновления не приходят):
1. Перезапуск автоматически вызовет `recover_webhook_processing()` в `main.py`.
2. Проверить redis inbox:
   ```bash
   docker exec infra-redis-1 redis-cli --tls \
     --cacert /home/deploy/infra/certs/server.crt \
     -a "$REDIS_PASSWORD" \
     llen webhook:inbox:updates
   ```
3. Если очередь растёт — проверить логи consumer'а.

---

## 6. DB advisory lock hang — deploy завис на миграциях

**Симптомы:** `docker compose ... up backend-api` висит >30s на "Running migrations" или migration-контейнер не заканчивает работу.

**Диагностика:**
```bash
# Найти какой PID держит advisory lock
docker exec infra-postgres-1 psql -U site_parser -d site_parser \
  -c "SELECT pid, query, state, wait_event_type, wait_event \
  FROM pg_stat_activity WHERE wait_event_type='Lock';"

# Посмотреть процесс что он делает
docker exec infra-postgres-1 psql -U site_parser -d site_parser \
  -c "SELECT pid, query_start, state, query FROM pg_stat_activity \
  WHERE pid = <PID>;"
```

**Решение:**

1. **Миграции имеют `lock_timeout=30s`:**
   - Если lock не отпущен за 30s, psql завершится с ошибкой.
   - Backend автоматически переретраивает (`MIGRATION_RETRY_ATTEMPTS=30` в env).

2. **Если предыдущий migration-контейнер завис:**
   ```bash
   docker ps | grep migrate
   docker stop <CONTAINER_ID>
   ```
   Это освободит advisory lock в Postgres.

3. **Если backend контейнер держит lock**:
   ```bash
   docker stop siteparser-backend-api-1
   # или более агрессивно:
   docker kill siteparser-backend-api-1
   ```
   Migrations освободятся и смогут пересоздаться.

4. **Если lock всё равно не отпущен**, убить зависший PID:
   ```bash
   docker exec infra-postgres-1 psql -U site_parser -d site_parser \
     -c "SELECT pg_terminate_backend(<PID>);"
   ```

5. **После освобождения lock'а** перезапустить deploy:
   ```bash
   docker compose -f /home/deploy/releases/current/docker-compose.prod.yml \
     -f /home/deploy/releases/current/docker-compose.ssl.yml \
     up -d backend-api
   ```

---

## Общие советы

- **Логи:** `docker logs <CONTAINER> --since <TIME>` — очень полезно для временных рамок.
- **Metrics:** Grafana dashboards — смотрите `Queues`, `API`, `Infra` перед deployment.
- **Alerts:** Alertmanager правила в `monitoring/alertmanager/` — добавляйте threshold'ы для new incident patterns.
- **Backup:** Ежедневные pgdump на `/home/deploy/shared/backups/` (см. `docs/vps_deploy.md`).
