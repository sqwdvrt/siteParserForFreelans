# Operations Guide

Документ закрывает операционный минимум перед релизом:
- мониторинг и алерты;
- release checklist;
- runbook инцидентов;
- DR/backup/restore процедуры.

## 0) Release Checklist

Чеклист привязан к текущему `Deploy Pipeline` (`.github/workflows/deploy.yml`) и должен проходиться перед production release вручную или через pipeline gates.

Для production-only шагов ниже используйте тот же compose-контур, что и deploy pipeline:
```bash
export PROD_COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml"
```

`$PROD_DEPLOY_PATH` и `$STAGING_DEPLOY_PATH` по-прежнему остаются операторскими точками входа: теперь это live symlink на `current`, а `current` уже указывает на активный release. Mutable state лежит в скрытой sibling-директории deploy state с `repo/`, `releases/`, `current`, `shared/.env.production` и `shared/backups/`, а release-local `.env.production` в live path уже содержит resolved digest-pinned image refs для текущего релиза.

> `docker-compose.ssl.yml` — VPS-overlay: подключает внешнюю сеть `infra_default` и пробрасывает CA-сертификат во все контейнеры. Требует запущенного `/home/deploy/infra`. Подробнее: `docs/vps_deploy.md`.

### 0.1 Preflight before deploy
1. Проверить target SHA и release notes.
2. Проверить, что обязательные gates на target SHA зелёные:
   - `Test Gate`
   - `Security Baseline`
   - `Supply Chain Security`
   - `Monitoring Gate`
3. Прогнать fail-fast preflight локально или убедиться, что job `release-preflight-gate` зелёный:
```bash
./scripts/release_preflight_gate.sh
```

### 0.1.1 AI-only hotfix rollout
Если нужен emergency rollout только для Python AI runtime, не собирайте image на VPS и не подменяйте `AI_IMAGE` локальным tag вручную.

Поддержанный путь:
1. Запустить `Deploy Pipeline` вручную (`.github/workflows/deploy.yml`).
2. Выбрать `target_environment=staging` или `production`.
3. Указать `ref` на target SHA с нужным AI fix.
4. Указать `deploy_scope=ai-only`.

Что делает workflow в этом режиме:
- собирает и публикует только digest-pinned `AI_IMAGE`;
- на хосте reuse'ит текущие `BACKEND_IMAGE`, `BROWSER_SERVICE_IMAGE`, `TELEGRAM_BOT_IMAGE` из live `.env.production`;
- отклоняет target commit, если он меняет что-то вне `ai-service/` и operator allowlist файлов deploy/runbook;
- вызывает тот же `scripts/deploy_release.sh`, поэтому release layout, `--no-build`, `current` symlink и post-deploy checks остаются каноническими.

Ограничения:
- это не bootstrap path: на хосте уже должен существовать live release с корректным `.env.production`;
- target `ref` должен быть отдельным AI-only commit, а не произвольным SHA с backend/compose/runtime изменениями.

Этот путь нужен именно для того, чтобы emergency AI fixes не обходили обычный immutable deploy flow.

### 0.2 Compose config
1. Проверить local/dev compose contract:
```bash
docker compose --env-file .env.example -f docker-compose.yml --profile workers --profile ai-user-embed config -q
```
2. Проверить production compose contract ровно так же, как его валидирует `release-preflight-gate`:
```bash
docker compose --env-file .env.production.example -f docker-compose.prod.yml config -q
```
3. Для текущего VPS дополнительно проверить overlay contract:
```bash
docker compose --env-file .env.production.example -f docker-compose.prod.yml -f docker-compose.ssl.yml config -q
```

### 0.3 Env validation
1. Проверить machine-readable production contract consistency:
```bash
python3 ./scripts/production_config_contract.py check-consistency
```
2. Проверить реальный production env перед деплоем:
```bash
bash ./scripts/validate-env-production.sh .env.production
```
3. Для `telegram-bot` подтвердить webhook ingress:
   `BOT_MODE=webhook`, `WEBHOOK_URL` и `WEBHOOK_SECRET_TOKEN` обязательны;
   host reverse proxy должен маршрутизировать `WEBHOOK_URL` на loopback-адрес `BOT_BIND_IP:BOT_PORT`.
   Готовый nginx-пример для VPS: `docs/vps_deploy.md` (`/webhook -> http://127.0.0.1:${BOT_PORT}`).
4. Если включается monitoring profile, подтвердить `GRAFANA_ADMIN_PASSWORD`.

### 0.4 Smoke and health
1. После staging deploy проверить health endpoint:
```bash
curl -fsS "$STAGING_HEALTHCHECK_URL"
```
2. Прогнать staging smoke/E2E gate только на staging host из deploy-директории,
где уже лежит `.env.production` и доступен локальный Docker daemon.
Скрипт требует `API_URL`, `API_AUTH_TOKEN`, `API_USER_HMAC_SECRET` и `docker`,
поэтому ручной запуск должен повторять контекст workflow:
```bash
cd "$STAGING_DEPLOY_PATH"
set -a
. ./.env.production
set +a

# optional: если smoke должен идти через отдельный staging URL
if [ -n "${STAGING_GATE_API_URL:-}" ]; then
  export API_URL="${STAGING_GATE_API_URL}"
fi

# optional: отдельный Telegram chat_id для smoke
if [ -n "${STAGING_SMOKE_TELEGRAM_ID:-}" ]; then
  export E2E_TELEGRAM_ID="${STAGING_SMOKE_TELEGRAM_ID}"
fi

bash ./scripts/staging_smoke_e2e_gate.sh
```
3. После production deploy проверить production health endpoint:
```bash
curl -fsS "$PROD_HEALTHCHECK_URL"
```
4. На production host прогнать post-deploy gate из deploy-директории, чтобы проверить health всех critical services, локальные `healthz/readyz` у backend API и webhook ingress:
```bash
cd "$PROD_DEPLOY_PATH"
set -a
. ./.env.production
set +a

bash ./scripts/post_deploy_production_gate.sh
```

### 0.5 Queue drain and post-release observation
1. Снять срез по очередям до и после production deploy:
```bash
redis-cli -u "$REDIS_URL" LLEN ai-process
redis-cli -u "$REDIS_URL" LLEN user-embed
redis-cli -u "$REDIS_URL" LLEN user-rematch
redis-cli -u "$REDIS_URL" LLEN ac-batch
redis-cli -u "$REDIS_URL" LLEN match-notify
```
2. Проверить `*:processing` и `*:dlq` на зависание/рост:
```bash
redis-cli -u "$REDIS_URL" LLEN ai-process:processing
redis-cli -u "$REDIS_URL" LLEN user-embed:processing
redis-cli -u "$REDIS_URL" LLEN user-rematch:processing
redis-cli -u "$REDIS_URL" LLEN ac-batch:processing
redis-cli -u "$REDIS_URL" LLEN match-notify:processing

redis-cli -u "$REDIS_URL" LLEN ai-process:dlq
redis-cli -u "$REDIS_URL" LLEN user-embed:dlq
redis-cli -u "$REDIS_URL" LLEN user-rematch:dlq
redis-cli -u "$REDIS_URL" LLEN ac-batch:dlq
redis-cli -u "$REDIS_URL" LLEN match-notify:dlq
```
3. В первые 10-15 минут после релиза проверить логи основных consumers:
```bash
$PROD_COMPOSE logs --since=15m backend-api
$PROD_COMPOSE logs --since=15m backend-crawler
$PROD_COMPOSE logs --since=15m backend-notifier
$PROD_COMPOSE logs --since=15m ai-service
$PROD_COMPOSE logs --since=15m ai-user-rematch
```

### 0.6 Rollback drill readiness
1. Убедиться, что есть свежий pre-release backup и известен путь к последнему рабочему dump.
2. Проверить, что команда restore/rollback известна заранее:
```bash
DATABASE_URL='postgres://...' ./scripts/restore_postgres.sh /path/to/pre_release.dump
```
3. Подтвердить, что rollback owner знает предыдущий рабочий image/tag и порядок остановки writers:
```bash
$PROD_COMPOSE stop backend-api backend-crawler backend-notifier ai-service ai-user-embed ai-user-rematch ai-ac-consumer telegram-bot
```
4. Не реже одного раза в месяц выполнить restore drill по разделу `4.4 Restore drill checklist`.

## 1) Monitoring

### 1.0 Базовая инфраструктура мониторинга в репозитории
- Prometheus compose профиль: `docker-compose.monitoring.yml`
- Prometheus template: `monitoring/prometheus/prometheus.yml.tmpl`
- Alert rules: `monitoring/prometheus/alerts.yml`
- Alertmanager config template: `monitoring/alertmanager/alertmanager.yml.tmpl`
- Grafana dashboards: `monitoring/grafana/dashboards/*.json`
- Grafana datasources: `monitoring/grafana/datasources/*.yml`
- Redis exporter: service `redis-exporter` (queue/Redis metrics via key lengths and exporter stats)
- Postgres exporter: service `postgres-exporter` (DB/pool/runtime metrics)
- CI-проверка конфигов: `.github/workflows/monitoring-gate.yml`

Для доставки алертов в каналы:
- `ALERTMANAGER_TELEGRAM_BOT_TOKEN` (fallback: `TELEGRAM_BOT_TOKEN`)
- `ALERTMANAGER_TELEGRAM_CHAT_ID` (fallback: `TELEGRAM_ID`)
- `ALERTMANAGER_SLACK_WEBHOOK_URL` (опционально)
- `GRAFANA_ADMIN_PASSWORD` (обязателен, если запускается Grafana)

Не храните токены в plaintext-файлах `monitoring/alertmanager/secrets/*`.
Если legacy-файлы уже созданы, удалите их:
`rm -f monitoring/alertmanager/secrets/telegram_bot_token monitoring/alertmanager/secrets/telegram_chat_id monitoring/alertmanager/secrets/slack_webhook_url`

### 1.0.1 Product analytics в Grafana
- SQL datasource для продуктовой аналитики provisioning'ится как `Product Analytics Postgres`.
- Базовый dashboard: `SiteParser — Product Analytics`.
- Источник данных: таблица PostgreSQL `product_events`.

Минимальный запуск локально:
```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml --profile monitoring up -d
```

Production monitoring запускай вместе с `docker-compose.prod.yml`, а не с dev-compose. Для этого переопредели:
- `BACKEND_API_METRICS_TARGET=backend-api:8443`
- `BACKEND_API_METRICS_SCHEME=https`
- `BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY=true`
- `POSTGRES_EXPORTER_DATA_SOURCE_NAME=postgresql://...?...sslmode=require`
- `GRAFANA_POSTGRES_HOST`, `GRAFANA_POSTGRES_PORT`, `GRAFANA_POSTGRES_SSLMODE=require`

На VPS добавь ещё `docker-compose.ssl.yml`, чтобы monitoring-контейнеры попали в `infra_default` и увидели self-hosted Redis/Postgres.

Пример:
```bash
docker compose --env-file .env.production \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl.yml \
  -f docker-compose.monitoring.yml \
  --profile monitoring up -d
```

После старта открыть:
- Grafana: `http://127.0.0.1:3000`
- Dashboard: `SiteParser — Product Analytics`

Что смотреть в первую очередь:
- `Registrations Today`
- `Profile Completion Rate`
- `Notification to Feedback Conversion`
- `Best Sources by Match Quality`

Текущий набор событий в `product_events`:
- `user_registered`
- `profile_updated`
- `profile_completed`
- `preferences_updated`
- `notify_hour_updated`
- `feedback_submitted`
- `notification_sent`

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
  В production backend API scrape идёт по HTTPS на внутренний `backend-api:8443`, поэтому monitoring stack должен знать TLS-target через `BACKEND_API_METRICS_*`.

Проверка:
```bash
curl -kfsS https://127.0.0.1:8443/healthz
curl -kfsS https://127.0.0.1:8443/readyz
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
  - состояние self-hosted инстанса на VPS.
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
- Workers: down (`backend-crawler`, `backend-notifier`, `ai-user-rematch`).
- Queues: backlog (`ai-process`, `user-embed`, `user-rematch`, `match-notify`), DLQ non-empty/growing, stall (`ai-process`, `backend-notifier`, `ai-user-rematch`).

## 3) Incident Runbook

### 3.1 Общий порядок (SEV1/SEV2)
1. Зафиксировать время начала инцидента.
2. Проверить `healthz/readyz`.
3. Проверить логи сервисов:
```bash
$PROD_COMPOSE logs --since=15m backend-api
$PROD_COMPOSE logs --since=15m backend-crawler
$PROD_COMPOSE logs --since=15m backend-notifier
$PROD_COMPOSE logs --since=15m ai-service
$PROD_COMPOSE logs --since=15m ai-user-embed
$PROD_COMPOSE logs --since=15m ai-user-rematch
$PROD_COMPOSE logs --since=15m telegram-bot
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
$PROD_COMPOSE restart backend-api
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
  - WAL/PITR можно добавить позже, если вы отдельно включите такую схему в self-hosted infra;
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

### 4.3.1 Автоматизация backup (cron / systemd timer)

`backup_vps_cron.sh` существует в `scripts/`, но его нужно подключить к планировщику на VPS.

**Вариант 1 — crontab (простой):**

```bash
# От пользователя deploy на VPS:
crontab -e
```

Добавить строку (ежедневно в 03:00):
```
0 3 * * * /home/deploy/app/siteParserForFreelans/scripts/backup_vps_cron.sh >> /var/log/siteparser-backup.log 2>&1
```

Проверить:
```bash
crontab -l
tail -f /var/log/siteparser-backup.log
```

**Вариант 2 — systemd timer (рекомендуется для надёжности):**

Создать `/etc/systemd/system/siteparser-backup.service`:
```ini
[Unit]
Description=SiteParser PostgreSQL Backup
After=docker.service

[Service]
Type=oneshot
User=deploy
ExecStart=/home/deploy/app/siteParserForFreelans/scripts/backup_vps_cron.sh
StandardOutput=journal
StandardError=journal
```

Создать `/etc/systemd/system/siteparser-backup.timer`:
```ini
[Unit]
Description=SiteParser Backup Timer

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Активировать:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now siteparser-backup.timer
systemctl list-timers siteparser-backup.timer
```

Проверить последний запуск:
```bash
journalctl -u siteparser-backup.service --since today
```

**Проверка backup:**
```bash
ls -lh /home/deploy/app/.siteParserForFreelans-deploy/shared/backups/
# Должны быть файлы site_parser_YYYY-MM-DD_*.dump
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
$PROD_COMPOSE stop backend-api backend-crawler backend-notifier ai-service ai-user-embed ai-user-rematch ai-ac-consumer telegram-bot
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
$PROD_COMPOSE up -d
curl -kfsS https://127.0.0.1:8443/healthz
curl -kfsS https://127.0.0.1:8443/readyz
./scripts/e2e_test.sh
```
8. Зафиксировать в инциденте: какой dump использован, RPO/RTO, какие миграции признаны проблемными.

Если полный restore недопустим:
- сделать forward-fix (новая миграция, исправляющая схему), это приоритетный путь;
- ручной SQL rollback допустим только для явно обратимых изменений (например, лишний индекс),
  и только после аварийного backup из шага 2.
