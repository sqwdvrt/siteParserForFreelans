# Operations Guide

Документ закрывает операционный минимум перед релизом:
- мониторинг и алерты;
- runbook инцидентов;
- DR/backup/restore процедуры.

## 1) Monitoring

### 1.0 Базовая инфраструктура мониторинга в репозитории
- Prometheus compose профиль: `docker-compose.monitoring.yml`
- Prometheus config: `monitoring/prometheus/prometheus.yml`
- Alert rules: `monitoring/prometheus/alerts.yml`
- Alertmanager config template: `monitoring/alertmanager/alertmanager.yml.tmpl`
- Redis exporter: service `redis-exporter` (queue/Redis metrics via key lengths and exporter stats)
- Postgres exporter: service `postgres-exporter` (DB/pool/runtime metrics)
- CI-проверка конфигов: `.github/workflows/monitoring-gate.yml`

Для доставки алертов в каналы:
- `ALERTMANAGER_TELEGRAM_BOT_TOKEN` (fallback: `TELEGRAM_BOT_TOKEN`)
- `ALERTMANAGER_TELEGRAM_CHAT_ID` (fallback: `TELEGRAM_ID`)
- `ALERTMANAGER_SLACK_WEBHOOK_URL` (опционально)

Не храните токены в plaintext-файлах `monitoring/alertmanager/secrets/*`.
Если legacy-файлы уже созданы, удалите их:
`rm -f monitoring/alertmanager/secrets/telegram_bot_token monitoring/alertmanager/secrets/telegram_chat_id monitoring/alertmanager/secrets/slack_webhook_url`

### 1.1 SLO (рекомендуемые целевые значения)
- API availability (5xx + timeout): `>= 99.9%` за 30 дней.
- API latency p95 (`POST /users`, `PUT /users/:id/profile`): `< 300ms`.
- End-to-end latency `job_saved -> notification_sent` (p95): `< 2 min`.
- Queue reliability (DLQ ratio): `< 0.5%` от общего потока.

### 1.2 Service health probes
- `GET /healthz`:
  назначение: liveness.
- `GET /readyz`:
  назначение: readiness (БД и Redis доступны).
- `GET /metrics`:
  назначение: scrape endpoint для Prometheus.

Проверка:
```bash
curl -fsS http://localhost:8080/healthz
curl -fsS http://localhost:8080/readyz
```

### 1.3 Метрики, которые нужно собирать
- API:
  - request rate (RPS), p50/p95 latency, 4xx/5xx, timeout count;
  - signature/nonce rejects;
  - rate-limit rejects.
- Redis:
  - queue depth: `ai-process`, `ai-process:processing`, `ai-process:dlq`;
  - queue depth: `user-embed`, `user-embed:processing`, `user-embed:dlq`;
  - queue depth: `match-notify`, `match-notify:processing`, `match-notify:dlq`.
- PostgreSQL:
  - active connections, long-running queries, deadlocks;
  - table/index bloat;
  - replication lag (если managed cluster).
- Business:
  - jobs parsed/hour;
  - matches/job;
  - notifications sent/day per user;
  - dedupe ratio.

Быстрый ручной срез по очередям:
```bash
redis-cli -u "$REDIS_URL" LLEN ai-process
redis-cli -u "$REDIS_URL" LLEN ai-process:processing
redis-cli -u "$REDIS_URL" LLEN ai-process:dlq

redis-cli -u "$REDIS_URL" LLEN user-embed
redis-cli -u "$REDIS_URL" LLEN user-embed:processing
redis-cli -u "$REDIS_URL" LLEN user-embed:dlq

redis-cli -u "$REDIS_URL" LLEN match-notify
redis-cli -u "$REDIS_URL" LLEN match-notify:processing
redis-cli -u "$REDIS_URL" LLEN match-notify:dlq
```

## 2) Alerts

Рекомендуемые стартовые алерты:
- `P1 API down`:
  `healthz`/`readyz` недоступен > 2 мин.
- `P1 DB unavailable`:
  API readiness = fail > 2 мин.
- `P1 Redis unavailable`:
  рост ошибок очередей или consumer stop.
- `P2 High 5xx`:
  5xx > 2% за 5 мин.
- `P2 Latency regression`:
  p95 > 500ms за 10 мин.
- `P2 Queue backlog`:
  `ai-process` или `match-notify` > 1000 сообщений > 10 мин.
- `P2 DLQ growth`:
  любой `*:dlq` > 0 (или рост > N/мин).
- `P2 Notification stall`:
  jobs идут, но notifications не отправляются > 15 мин.

Текущая реализация в `monitoring/prometheus/alerts.yml` включает:
- API: down, high 5xx, high p95 latency.
- Infra: redis/postgres exporter down, redis/postgres unavailable, postgres connections high.
- Queues: backlog (`ai-process`, `user-embed`, `match-notify`), DLQ non-empty/growing, stall (`ai-process`, `backend-notifier`).

## 3) Incident Runbook

### 3.1 Общий порядок (SEV1/SEV2)
1. Зафиксировать время начала инцидента.
2. Проверить `healthz/readyz`.
3. Проверить логи сервисов:
```bash
docker compose logs --since=15m backend-api
docker compose logs --since=15m backend-crawler
docker compose logs --since=15m backend-notifier
docker compose logs --since=15m ai-service
docker compose logs --since=15m ai-user-embed
docker compose logs --since=15m telegram-bot
```
4. Проверить очереди (`LLEN`) и DLQ.
5. Если проблема не устраняется быстро: деградация/ограничение трафика, затем rollback.
6. После восстановления: postmortem с root cause и action items.

### 3.2 API недоступен
1. Проверить container status.
2. Проверить TLS/сертификаты (production).
3. Проверить доступность PostgreSQL/Redis.
4. Перезапуск:
```bash
docker compose restart backend-api
```

### 3.3 Очереди растут, уведомления не отправляются
1. Проверить `match-notify` и `match-notify:processing`.
2. Проверить `backend-notifier` логи на Telegram API ошибки.
3. Проверить rate-limit/daily-limit настройки.
4. После фикса обработать backlog (обычно consumer сам догоняет).

### 3.4 Рост DLQ
1. Выгрузить 10–50 последних сообщений из DLQ.
2. Определить класс ошибки (payload/schema/network/transient).
3. Исправить причину.
4. Репроцесс:
```bash
# пример для match-notify
for i in $(seq 1 100); do
  v=$(redis-cli -u "$REDIS_URL" RPOPLPUSH match-notify:dlq match-notify)
  [ -z "$v" ] && break
done
```

## 4) DR, Backup, Restore

### 4.1 Цели восстановления
- RPO: `<= 15 min` (потеря данных).
- RTO: `<= 60 min` (время восстановления сервиса).

### 4.2 Backup policy
- PostgreSQL:
  - еженощный full backup (`pg_dump -Fc`);
  - WAL/PITR у managed Postgres (если доступно);
  - retention не меньше 14 дней.
- Redis:
  - для очередей можно принимать потерю transient-сообщений;
  - если требуется строгая гарантия, включить AOF/snapshot + внешнее хранилище.
- Проверка backup:
  - ежедневная проверка checksum;
  - минимум 1 restore drill в месяц.

### 4.3 Скрипты
- Backup:
  `scripts/backup_postgres.sh`
- Restore:
  `scripts/restore_postgres.sh`

Пример backup:
```bash
DATABASE_URL='postgres://...' ./scripts/backup_postgres.sh
```

Пример restore:
```bash
DATABASE_URL='postgres://...' ./scripts/restore_postgres.sh /path/to/backup.dump
```

### 4.4 Restore drill checklist
1. Поднять чистую test/staging БД.
2. Выполнить restore из свежего backup.
3. Запустить smoke:
   - `SELECT COUNT(*)` по `users/jobs/notifications`;
   - `docker compose up` + `./scripts/e2e_test.sh`.
4. Зафиксировать `actual RTO/RPO`.

### 4.5 Manual rollback for DB migrations (forward-only)
В репозитории нет `down`-миграций: `backend/migrations/*.sql` применяются только вперёд.
Rollback делается вручную по runbook ниже.

Триггеры:
- после релиза появилась ошибка SQL/схемы;
- сервисы падают на запросах к новым колонкам/индексам;
- `backend-migrate` не может завершить миграции.

Порядок rollback:
1. Остановить запись в БД (writers), чтобы зафиксировать состояние:
```bash
docker compose stop backend-api backend-crawler backend-notifier ai-service ai-user-embed telegram-bot
```
2. Снять аварийный backup текущего (даже «плохого») состояния:
```bash
DATABASE_URL='postgres://...' BACKUP_DIR='./backups/emergency' ./scripts/backup_postgres.sh
```
3. Выбрать последний гарантированно рабочий dump (pre-release backup) и проверить checksum.
4. Выполнить restore:
```bash
DATABASE_URL='postgres://...' ./scripts/restore_postgres.sh /path/to/pre_release.dump
```
5. Откатить приложение на предыдущий release image/tag (где проблемной миграции ещё нет).
6. Поднять `backend-migrate` и убедиться, что миграции завершаются без ошибок.
7. Поднять сервисы и выполнить smoke:
```bash
curl -fsS http://localhost:8080/healthz
curl -fsS http://localhost:8080/readyz
./scripts/e2e_test.sh
```
8. Зафиксировать в инциденте: какой dump использован, RPO/RTO, какие миграции признаны проблемными.

Если полный restore недопустим:
- сделать forward-fix (новая миграция, исправляющая схему), это приоритетный путь;
- ручной SQL rollback допустим только для явно обратимых изменений (например, лишний индекс),
  и только после аварийного backup из шага 2.
