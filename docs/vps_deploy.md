# VPS Deployment Guide

Руководство по текущему production-развёртыванию проекта на одном VPS.

Текущая схема:
- приложение запускается из release-каталога под `/home/deploy/app/.siteParserForFreelans-deploy/current`;
- PostgreSQL и Redis живут на том же VPS в `/home/deploy/infra`;
- публичный вход идет через nginx на `api.freematch.ru`;
- `/webhook` проксируется в `http://127.0.0.1:8080`, а остальной трафик в `https://127.0.0.1:8443`;
- мониторинг и `pgAdmin` доступны только локально на loopback и через SSH tunnel.

## 1. Требования

- VPS с Ubuntu 22.04+, минимум 2 GB RAM, 20 GB диска
- Docker + Docker Compose v2
- Домен `api.freematch.ru`, указывающий на IP VPS
- Пользователь `deploy` с правами sudo

## 2. Swap

AI-сервисы загружают embedding-модели и на 2 GB VPS без swap это нестабильно. Настрой swap до запуска контейнеров:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

Проверка:

```bash
free -h
swapon --show
```

## 3. Public TLS + nginx

На VPS публичный TLS заканчивается на nginx. Внутри nginx используются обычные Let’s Encrypt сертификаты, а к локальным контейнерам он ходит по loopback.

```bash
sudo apt install -y nginx certbot
sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw allow 22/tcp
sudo certbot certonly --standalone -d api.freematch.ru
```

Конфиг `/etc/nginx/sites-available/api.freematch.ru`:

```nginx
server {
    listen 80;
    server_name api.freematch.ru;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name api.freematch.ru;

    ssl_certificate     /etc/letsencrypt/live/api.freematch.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.freematch.ru/privkey.pem;

    location = /webhook {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass         https://127.0.0.1:8443;
        proxy_ssl_verify   off;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Активировать конфиг:

```bash
sudo ln -s /etc/nginx/sites-available/api.freematch.ru /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl enable --now nginx
```

Проверка ingress:

```bash
curl -fsS https://api.freematch.ru/healthz
```

## 4. Self-hosted infra

Инфраструктура живет отдельно в `/home/deploy/infra`.

### 4.1 TLS для internal services

Сертификат нужен только для доверия между контейнерами `postgres` и `redis`.

```bash
mkdir -p /home/deploy/infra/certs

docker run --rm \
  -v /home/deploy/infra/certs:/certs \
  --user root \
  alpine \
  sh -c "
    apk add --no-cache openssl && \
    openssl req -new -x509 -days 3650 -nodes \
      -out /certs/server.crt \
      -keyout /certs/server.key \
      -subj '/CN=internal' \
      -addext 'subjectAltName=DNS:postgres,DNS:redis,DNS:localhost,IP:127.0.0.1' && \
    chown 999:999 /certs/server.key /certs/server.crt && \
    chmod 600 /certs/server.key && chmod 644 /certs/server.crt
  "
```

### 4.2 Infra env

```bash
cat > /home/deploy/infra/.env << EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
EOF
```

### 4.3 Infra compose

Файл `/home/deploy/infra/docker-compose.yml` поднимает:
- `postgres` на `127.0.0.1:5432`
- `redis` на `127.0.0.1:6379`

Оба сервиса работают с TLS и доступны app-контейнерам через сеть `infra_default`.

```bash
cd /home/deploy/infra
docker network create infra_default 2>/dev/null || true
docker compose up -d
```

Проверка:

```bash
docker exec infra-postgres-1 psql -U site_parser -d site_parser -c "SHOW ssl;"
docker compose logs redis | grep "Ready to accept connections tls"
```

### 2.1 Redis persistence

Redis используется как **durable state store** (очереди задач, webhook-inbox Telegram-бота). При рестарте без persistence данные теряются.

Конфигурация уже включена в `/home/deploy/infra/docker-compose.yml` (параметры `--appendonly yes`, `--maxmemory-policy allkeys-lru`, том `redisdata:/data`). Проверить после рестарта:

```bash
docker exec infra-redis-1 redis-cli --tls \
  --cert /tls/server.crt --key /tls/server.key --cacert /tls/server.crt \
  -a <REDIS_PASSWORD> INFO persistence | grep aof_enabled
# → aof_enabled:1
```

PostgreSQL — источник истины для всех пользовательских данных. Redis — дополнительный persistence слой для task queues и webhook-inbox Telegram-бота (AOF enabled). Потеря Redis данных приведёт к повторной обработке части событий, но не к потере пользовательских данных.

---

## 5. App deployment

Текущий layout на VPS:

```text
/home/deploy/app/
├── .siteParserForFreelans-deploy/
│   ├── repo/
│   ├── releases/
│   ├── current -> releases/<current release>
│   └── shared/
│       ├── .env.production
│       └── backups/
└── siteParserForFreelans -> .siteParserForFreelans-deploy/current
```

Операторский путь всегда остается `cd /home/deploy/app/siteParserForFreelans`, но это live symlink на active release.

Базовые production env values на VPS должны соответствовать self-hosted infra:

```env
APP_ENV=production
API_URL=https://api.freematch.ru
WEBHOOK_URL=https://api.freematch.ru/webhook
WEBHOOK_SECRET_TOKEN=<output of: openssl rand -hex 32>
BOT_MODE=webhook
BOT_BIND_IP=127.0.0.1
BOT_PORT=8080
DATABASE_URL=postgresql://site_parser:<POSTGRES_PASSWORD>@postgres:5432/site_parser?sslmode=require
DATABASE_MIGRATE_URL=postgresql://site_parser:<POSTGRES_PASSWORD>@postgres:5432/site_parser?sslmode=require
REDIS_URL=rediss://:<REDIS_PASSWORD>@redis:6379/0
BACKEND_IMAGE=ghcr.io/<owner>/siteparserforfreelans-backend@sha256:<64-hex-digest>
BROWSER_SERVICE_IMAGE=ghcr.io/<owner>/siteparserforfreelans-browser-service@sha256:<64-hex-digest>
TELEGRAM_BOT_IMAGE=ghcr.io/<owner>/siteparserforfreelans-telegram-bot@sha256:<64-hex-digest>
AI_IMAGE=ghcr.io/<owner>/siteparserforfreelans-ai-runtime@sha256:<64-hex-digest>
API_AUTH_TOKEN=<output of: openssl rand -hex 24>
API_USER_HMAC_SECRET=<output of: openssl rand -hex 24>
ADMIN_AUTH_TOKEN=<output of: openssl rand -hex 24>
API_TLS_CERT_HOST_PATH=/etc/letsencrypt/live/api.freematch.ru/fullchain.pem
API_TLS_KEY_HOST_PATH=/etc/letsencrypt/live/api.freematch.ru/privkey.pem
PGADMIN_EMAIL=admin@example.com
PGADMIN_PASSWORD=<output of: openssl rand -hex 16>
GRAFANA_ADMIN_PASSWORD=<output of: openssl rand -hex 16>
```

Bootstrap нового VPS делайте через `Deploy Pipeline` или `scripts/deploy_release.sh`, чтобы release layout (`shared/`, `releases/`, `current`) и release-local `.env.production` были собраны автоматически. Ручной `docker compose ... up` ниже подходит только для уже активного live release, где `.env.production` уже содержит digest-pinned image refs.

Запуск production stack из live release:

```bash
cd /home/deploy/app/siteParserForFreelans
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml pull
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml up -d --no-build
```

Проверить сервисы:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml config --services
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Точечный AI-only hotfix делайте не через `docker build` на VPS, а через manual run `Deploy Pipeline` с `deploy_scope=ai-only`. В этом режиме workflow публикует новый digest-pinned `AI_IMAGE`, reuse'ит текущие non-AI image refs с хоста и прогоняет тот же release-based deploy path.

Ограничения этого режима:
- нужен уже существующий live release с корректным `.env.production`; для первого deploy используйте обычный full deploy path;
- target SHA должен быть AI-only commit: workflow отклонит `ai-only` rollout, если commit меняет что-то вне `ai-service/` и allowlist файлов deploy/runbook.

## 6. pgAdmin

`pgAdmin` в production compose есть как optional admin profile и слушает только loopback на VPS.

Запуск:

```bash
cd /home/deploy/app/siteParserForFreelans
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml --profile admin up -d pgadmin
```

SSH tunnel с локальной машины:

```bash
ssh -N -L 5050:127.0.0.1:5050 deploy@185.154.193.193
```

Открыть в браузере:

```text
http://127.0.0.1:5050
```

В `pgAdmin` подключайся к базе так:
- Host: `postgres`
- Port: `5432`
- Database: `site_parser`
- Username: `site_parser`
- Password: `POSTGRES_PASSWORD` из `/home/deploy/infra/.env`
- SSL mode: `Require`

## 7. Monitoring

Monitoring запускается на том же VPS и тоже использует `infra_default`.

```bash
cd /home/deploy/app/siteParserForFreelans
docker compose --env-file .env.production \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl.yml \
  -f docker-compose.monitoring.yml \
  --profile monitoring up -d
```

Доступ только локально:
- Grafana: `http://127.0.0.1:3000`
- Prometheus: `http://127.0.0.1:9090`
- Alertmanager: `http://127.0.0.1:9093`

Логин Grafana:
- User: `admin`
- Password: `GRAFANA_ADMIN_PASSWORD` из `.env.production`

Production monitoring overrides должны указывать на:
- `backend-api:8443`
- `rediss://...`
- `postgresql://...sslmode=require`

## 8. Backup and restore

PostgreSQL - источник истины. Redis - transient queues, его обычно не восстанавливают как полноценный state store.

Рекомендуемый backup:

```bash
cd /home/deploy/app/siteParserForFreelans
mkdir -p /home/deploy/app/.siteParserForFreelans-deploy/shared/backups
set -a
. ./.env.production
set +a
BACKUP_DIR=/home/deploy/app/.siteParserForFreelans-deploy/shared/backups ./scripts/backup_postgres.sh
```

Restore:

```bash
cd /home/deploy/app/siteParserForFreelans
set -a
. ./.env.production
set +a
./scripts/restore_postgres.sh /path/to/site_parser.dump
```

Проверка после restore:

```bash
docker exec infra-postgres-1 psql -U site_parser -d site_parser -c "SELECT count(*) FROM public.users;"
docker exec infra-postgres-1 psql -U site_parser -d site_parser -c "SELECT count(*) FROM public.notifications;"
```

## 9. Quick checklist

1. `infra-postgres-1` и `infra-redis-1` запущены.
2. `backend-api` отвечает на `/healthz` через nginx.
3. `/webhook` идет в `127.0.0.1:8080`.
4. `pgAdmin` доступен только через SSH tunnel.
5. `Prometheus`, `Grafana`, `Alertmanager` слушают только loopback.
