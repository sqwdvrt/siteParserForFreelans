# VPS Deployment Guide

Руководство по развёртыванию проекта на чистом VPS с самоподписанными TLS-сертификатами.

## Требования

- VPS с Ubuntu 22.04+, минимум 2 GB RAM, 20 GB диска
- Docker + Docker Compose v2
- Домен с A-записью на IP VPS (freematch.ru → 185.154.193.193)
- Пользователь `deploy` с правами sudo

---

## 1. Nginx + TLS (Let's Encrypt)

```bash
sudo apt install -y nginx certbot

# Открыть порты
sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw allow 22/tcp

# Получить сертификат (порт 80 должен быть свободен)
sudo certbot certonly --standalone -d api.freematch.ru
```

Certbot оставляет `privkey.pem` недоступным для world-readable доступа, и для текущего nginx это нормально: master-process читает TLS-ключ как root при старте. Не делай `chmod 644` для приватного ключа. Если когда-то понадобится доступ к ключу из non-root процесса, используй точечный `group ownership` или ACL только для конкретного читателя, а не общий read для всех.

Конфиг nginx `/etc/nginx/sites-available/api.freematch.ru`:

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

    # Telegram webhook must bypass backend-api and go straight to telegram-bot.
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

```bash
sudo ln -s /etc/nginx/sites-available/api.freematch.ru /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl enable --now nginx
```

`telegram-bot` в production слушает loopback `BOT_BIND_IP:BOT_PORT` (`127.0.0.1:8080` по умолчанию) по plain HTTP, поэтому nginx должен проксировать `https://api.freematch.ru/webhook` именно в `http://127.0.0.1:8080`, а не в backend API. Для `.env.production` это соответствует:

```env
API_URL=https://api.freematch.ru
BOT_MODE=webhook
BOT_BIND_IP=127.0.0.1
BOT_PORT=8080
WEBHOOK_URL=https://api.freematch.ru/webhook
```

---

## 2. Инфраструктура (Postgres + Redis)

```bash
mkdir -p /home/deploy/infra/certs

# Генерация самоподписанного сертификата с SAN для postgres и redis
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

# Пароли
cat > /home/deploy/infra/.env << EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
EOF
```

`/home/deploy/infra/docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_USER: site_parser
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: site_parser
    command: >
      postgres
      -c ssl=on
      -c ssl_cert_file=/etc/ssl/certs/server.crt
      -c ssl_key_file=/etc/ssl/private/server.key
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./certs/server.crt:/etc/ssl/certs/server.crt:ro
      - ./certs/server.key:/etc/ssl/private/server.key:ro
    ports:
      - "127.0.0.1:5432:5432"

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: >
      redis-server
      --requirepass ${REDIS_PASSWORD}
      --appendonly yes
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
      --tls-port 6379
      --port 0
      --tls-cert-file /tls/server.crt
      --tls-key-file /tls/server.key
      --tls-ca-cert-file /tls/server.crt
      --tls-auth-clients no
    volumes:
      - redisdata:/data
      - ./certs/server.crt:/tls/server.crt:ro
      - ./certs/server.key:/tls/server.key:ro
    ports:
      - "127.0.0.1:6379:6379"

volumes:
  pgdata:
  redisdata:
```

```bash
cd /home/deploy/infra && docker compose up -d

# Проверить SSL у postgres
docker exec infra-postgres-1 psql -U site_parser -d site_parser -c "SHOW ssl;"
# Должно вернуть: on

# Проверить TLS у redis
docker compose logs redis | grep "Ready to accept connections tls"
```

---

## 3. Миграция данных из Supabase

```bash
# Дамп без таблицы jobs (из-за SSL timeout на большой таблице)
docker run --rm \
  -v /home/deploy:/dump \
  postgres:17 \
  pg_dump \
  "postgresql://<supabase-pooler-url>" \
  --no-owner --no-acl \
  --exclude-table-data=public.jobs \
  --format=custom \
  -f /dump/supabase_dump.dump

# Дамп только jobs
docker run --rm \
  -v /home/deploy:/dump \
  postgres:17 \
  pg_dump \
  "postgresql://<supabase-pooler-url>" \
  --no-owner --no-acl \
  --table=public.jobs \
  --format=custom \
  -f /dump/jobs_dump.dump

# Восстановить (ошибки pg_graphql/supabase_vault — ignorable, это Supabase-расширения)
docker run --rm \
  -v /home/deploy:/dump \
  --network infra_default \
  -e PGPASSWORD=<POSTGRES_PASSWORD> \
  postgres:17 \
  pg_restore \
  -h 172.18.0.2 -p 5432 \
  -U site_parser -d site_parser \
  --no-owner --no-acl \
  /dump/supabase_dump.dump

docker run --rm \
  -v /home/deploy:/dump \
  --network infra_default \
  -e PGPASSWORD=<POSTGRES_PASSWORD> \
  postgres:17 \
  pg_restore \
  -h 172.18.0.2 -p 5432 \
  -U site_parser -d site_parser \
  --no-owner --no-acl \
  --data-only \
  /dump/jobs_dump.dump

# Проверить
docker exec infra-postgres-1 psql -U site_parser -d site_parser -c "
  SELECT 'jobs' as t, count(*) FROM public.jobs
  UNION ALL SELECT 'users', count(*) FROM public.users
  UNION ALL SELECT 'notifications', count(*) FROM public.notifications;"
```

---

## 4. Приложение

```bash
cd /home/deploy/app
git clone <repo-url> siteParserForFreelans
cd siteParserForFreelans
git checkout develop
```

Создать `.env.production` (заполнить по образцу `.env.production.example`):

```bash
# Ключевые значения для VPS:
DATABASE_URL=postgresql://site_parser:<POSTGRES_PASSWORD>@postgres:5432/site_parser?sslmode=require
DATABASE_MIGRATE_URL=postgresql://site_parser:<POSTGRES_PASSWORD>@postgres:5432/site_parser?sslmode=require
REDIS_URL=rediss://:<REDIS_PASSWORD>@redis:6379/0
API_TLS_CERT_HOST_PATH=/etc/letsencrypt/live/api.freematch.ru/fullchain.pem
API_TLS_KEY_HOST_PATH=/etc/letsencrypt/live/api.freematch.ru/privkey.pem
API_ADDR=:8443
API_PORT=8443
API_URL=https://api.freematch.ru
```

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.ssl.yml --env-file .env.production up -d --build
```

### 4.1 pgAdmin (optional, localhost-only)

`pgAdmin` можно поднять в том же production compose-контуре для ручной диагностики БД.
Сервис публикуется только на loopback хоста (`127.0.0.1:5050`), поэтому наружу не торчит
и предполагает доступ через SSH tunnel. По умолчанию сервис не стартует вместе с основным
production стеком: он вынесен в profile `admin` и запускается только явно.

Добавьте в `.env.production`:

```env
PGADMIN_EMAIL=admin@yourdomain.com
PGADMIN_PASSWORD=<output of: openssl rand -hex 16>
```

Запуск:

```bash
cd /home/deploy/app/siteParserForFreelans
docker compose -f docker-compose.prod.yml -f docker-compose.ssl.yml --env-file .env.production --profile admin up -d pgadmin
```

SSH tunnel с локальной машины:

```bash
ssh -L 5050:localhost:5050 deploy@185.154.193.193
```

После этого откройте `http://localhost:5050` и войдите под `PGADMIN_EMAIL` / `PGADMIN_PASSWORD`.

Для подключения к PostgreSQL внутри `pgAdmin` используйте:

- Host: `postgres`
- Port: `5432`
- Database: `site_parser`
- Username: `site_parser`
- Password: значение `POSTGRES_PASSWORD` из `/home/deploy/infra/.env`
- SSL mode: `Require`

### 4.2 Monitoring stack

Мониторинг на VPS поднимай в том же production compose-контуре, что и приложение: `docker-compose.prod.yml` + `docker-compose.ssl.yml` + `docker-compose.monitoring.yml`.
Это сохраняет общую `infra_default` сеть для доступа к self-hosted Redis/Postgres, даёт monitoring-контейнерам тот же CA и соответствует production contract.

```bash
cd /home/deploy/app/siteParserForFreelans
docker compose --env-file .env.production \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl.yml \
  -f docker-compose.monitoring.yml \
  --profile monitoring up -d
```

Перед запуском мониторинга проверь тот же production render-контракт:
```bash
docker compose --env-file .env.production.example \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl.yml \
  -f docker-compose.monitoring.yml \
  --profile monitoring config -q
```

Если приложение и мониторинг запускаешь разными командами, используй один и тот же `-p <project>` на обеих.

---

## 5. Проверка

```bash
curl -s https://api.freematch.ru/healthz
# Ожидается: ok
```

---

## 6. Управление сервисами

```bash
# Алиас для удобства (добавить в ~/.bashrc)
export PROD_COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml"

# Статус
$PROD_COMPOSE ps

# Логи
$PROD_COMPOSE logs <service> -f --tail=50

# Перезапуск
$PROD_COMPOSE restart <service>

# Полный рестарт
$PROD_COMPOSE up -d

# Очереди Redis
docker exec infra-redis-1 redis-cli --tls \
  --cert /tls/server.crt --key /tls/server.key --cacert /tls/server.crt \
  -a <REDIS_PASSWORD> LLEN ai-process
```

---

## Схема развёртывания

```
VPS (185.154.193.193)
├── nginx (443 → https://127.0.0.1:8443)
│
├── /home/deploy/infra/          ← postgres:5432 + redis:6379 (оба с TLS)
│   ├── docker-compose.yml
│   ├── .env
│   └── certs/server.crt|key    ← самоподписанный CA (CN=internal, SAN: postgres, redis)
│
└── /home/deploy/app/siteParserForFreelans/
    ├── docker-compose.prod.yml
    ├── docker-compose.ssl.yml   ← overlay: infra_default network + SSL_CERT_FILE
    └── .env.production
```

Все app-контейнеры подключены к сети `infra_default` и обращаются к `postgres:5432` / `redis:6379` по имени сервиса.
