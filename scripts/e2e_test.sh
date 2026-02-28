#!/bin/bash
# E2E тест: 9.7–9.9 — запуск системы, /start, /profile, crawl, AI, уведомление.
# Требует: docker compose, curl, psql (или docker exec), redis-cli (или docker exec)
# .env: DATABASE_URL, REDIS_URL, API_AUTH_TOKEN, API_USER_HMAC_SECRET, TELEGRAM_BOT_TOKEN (для 9.9)

set -e
cd "$(dirname "$0")/.."

# Keep caller-provided overrides before loading .env.
CLI_TELEGRAM_ID="${TELEGRAM_ID-}"
CLI_E2E_TELEGRAM_ID="${E2E_TELEGRAM_ID-}"
[ -f .env ] && set -a && source .env && set +a

API_URL="${API_URL:-http://localhost:8080}"
DB_URL="${DATABASE_URL:-postgres://site_parser:site_parser@localhost:55432/site_parser?sslmode=disable}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
AUTH_TOKEN="${API_AUTH_TOKEN:-}"
USER_HMAC_SECRET="${API_USER_HMAC_SECRET:-}"
PGUSER="${POSTGRES_USER:-site_parser}"
PGDB="${POSTGRES_DB:-site_parser}"
PGPASS="${POSTGRES_PASSWORD:-site_parser}"

if [ -z "$AUTH_TOKEN" ]; then
  echo "API_AUTH_TOKEN не задан. Загрузите .env или export API_AUTH_TOKEN=..."
  exit 1
fi
if [ -z "$USER_HMAC_SECRET" ]; then
  echo "API_USER_HMAC_SECRET не задан. Загрузите .env или export API_USER_HMAC_SECRET=..."
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl не найден, нужен для HMAC-подписи E2E-запросов"
  exit 1
fi
if ! command -v xxd >/dev/null 2>&1; then
  echo "xxd не найден, нужен для hex-кодирования HMAC-подписи"
  exit 1
fi

generate_nonce() {
  openssl rand -hex 16
}

sign_user_request() {
  local method="$1"
  local path="$2"
  local telegram_id="$3"
  local timestamp="$4"
  local nonce="$5"
  local body="$6"
  local body_hash payload
  body_hash="$(printf '%s' "$body" | openssl dgst -sha256 -binary | xxd -p -c 256)"
  payload="${method}
${path}
${telegram_id}
${timestamp}
${nonce}
${body_hash}"
  printf '%s' "$payload" | openssl dgst -sha256 -hmac "$USER_HMAC_SECRET" -binary | xxd -p -c 256
}

psql_compose() {
  docker compose exec -T -e PGPASSWORD="$PGPASS" postgres psql -U "$PGUSER" -d "$PGDB" "$@"
}

wait_for_compose_health() {
  local service="$1"
  local timeout_sec="${2:-180}"
  local required="${3:-1}"
  local elapsed=0
  local container_id
  container_id="$(docker compose ps -q "$service" | head -n1)"

  if [ -z "$container_id" ]; then
    if [ "$required" = "0" ]; then
      echo "INFO: сервис '$service' не запущен (optional), пропускаем ожидание"
      return 0
    fi
    echo "ERROR: контейнер сервиса '$service' не найден"
    exit 1
  fi

  echo "Ожидание readiness сервиса '$service' (timeout ${timeout_sec}s)..."
  while [ "$elapsed" -lt "$timeout_sec" ]; do
    local status
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$container_id" 2>/dev/null || echo "unknown")"
    if [ "$status" = "healthy" ]; then
      echo "✓ $service healthy"
      return 0
    fi
    if [ "$status" = "unhealthy" ]; then
      echo "ERROR: $service unhealthy"
      docker compose logs --tail=120 "$service" || true
      exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  echo "ERROR: timeout ожидания healthy для '$service'"
  docker compose logs --tail=120 "$service" || true
  exit 1
}

echo "=== E2E: Запуск docker compose ==="
docker compose --profile workers up -d
wait_for_compose_health "ai-service" 180
wait_for_compose_health "ai-user-embed" 180 0
echo "Ожидание API (10 сек)..."
sleep 10

echo ""
echo "=== 9.7: POST /users (/start) → User в БД ==="
# Telegram ID priority: E2E_TELEGRAM_ID -> caller TELEGRAM_ID -> .env TELEGRAM_ID -> fallback.
if [ -n "${CLI_TELEGRAM_ID}" ]; then
  TELEGRAM_ID="${CLI_TELEGRAM_ID}"
fi
if [ -n "${CLI_E2E_TELEGRAM_ID}" ]; then
  E2E_TELEGRAM_ID="${CLI_E2E_TELEGRAM_ID}"
fi
TELEGRAM_ID="${E2E_TELEGRAM_ID:-${TELEGRAM_ID:-123456789}}"
POST_BODY="{\"telegram_id\":$TELEGRAM_ID}"
POST_TS="$(date +%s)"
POST_NONCE="$(generate_nonce)"
POST_SIG="$(sign_user_request "POST" "/users" "$TELEGRAM_ID" "$POST_TS" "$POST_NONCE" "$POST_BODY")"
RESP=$(curl -s -w "\n%{http_code}" -X POST "$API_URL/users" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -H "X-Telegram-ID: $TELEGRAM_ID" \
  -H "X-Request-Timestamp: $POST_TS" \
  -H "X-Request-Nonce: $POST_NONCE" \
  -H "X-Request-Signature: $POST_SIG" \
  -H "Content-Type: application/json" \
  -d "$POST_BODY")
HTTP_CODE=$(echo "$RESP" | tail -n1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP_CODE" != "200" ]; then
  echo "POST /users failed: HTTP $HTTP_CODE, body: $BODY"
  exit 1
fi
USER_ID=$(echo "$BODY" | grep -o '"user_id":[0-9]*' | cut -d: -f2)
echo "User создан: user_id=$USER_ID"

# Проверка в БД
USER_COUNT=$(psql_compose -t -c "SELECT COUNT(*) FROM users WHERE telegram_id=$TELEGRAM_ID" 2>/dev/null | tr -d ' \n' || echo "0")
if [ "${USER_COUNT:-0}" -lt 1 ]; then
  echo "Проверка: User не найден в БД"
  exit 1
fi
echo "✓ User в БД"

echo ""
echo "=== 9.8: PUT /profile, crawl, AI (job_embeddings, matching) ==="
# PUT /profile
PUT_BODY='{"profile_text":"Python developer, 5 years"}'
PUT_PATH="/users/$USER_ID/profile"
PUT_TS="$(date +%s)"
PUT_NONCE="$(generate_nonce)"
PUT_SIG="$(sign_user_request "PUT" "$PUT_PATH" "$TELEGRAM_ID" "$PUT_TS" "$PUT_NONCE" "$PUT_BODY")"
curl -s -o /dev/null -w "%{http_code}" -X PUT "$API_URL/users/$USER_ID/profile" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -H "X-Telegram-ID: $TELEGRAM_ID" \
  -H "X-Request-Timestamp: $PUT_TS" \
  -H "X-Request-Nonce: $PUT_NONCE" \
  -H "X-Request-Signature: $PUT_SIG" \
  -H "Content-Type: application/json" \
  -d "$PUT_BODY" | grep -q 204 || { echo "PUT /profile failed"; exit 1; }
echo "✓ PUT /profile"

# Вставка тестового job и отправка в ai-process (имитация crawl)
JOB_SQL="INSERT INTO jobs (source, url, title, description, raw_html) VALUES ('kwork', 'https://kwork.ru/projects/e2e-test-'||floor(random()*1e9)||'/view', 'E2E Test Job', 'Python project', '<p>test</p>') RETURNING id"
JOB_ID=$(psql_compose -qAt -c "$JOB_SQL" 2>/dev/null | head -n1 | tr -d '\r')
JOB_ID=$(echo "$JOB_ID" | grep -Eo '^[0-9]+$' || true)
if [ -z "$JOB_ID" ]; then
  echo "Не удалось вставить job"
  exit 1
fi
echo "Job создан: job_id=$JOB_ID"

# Push в ai-process
docker compose exec -T redis redis-cli LPUSH ai-process "{\"job_id\":$JOB_ID}" >/dev/null 2>&1 || \
  redis-cli -u "$REDIS_URL" LPUSH ai-process "{\"job_id\":$JOB_ID}" >/dev/null 2>&1
echo "✓ Job в очереди ai-process"

# Ожидание обработки AI (модель может загружаться дольше на холодном старте)
echo "Ожидание AI (до 180 сек)..."
EMB_COUNT="0"
for _ in $(seq 1 18); do
  EMB_COUNT=$(psql_compose -t -c "SELECT COUNT(*) FROM job_embeddings WHERE job_id=$JOB_ID" 2>/dev/null | tr -d ' \n' || echo "0")
  if [ "${EMB_COUNT:-0}" -ge 1 ]; then
    break
  fi
  sleep 10
done

# Проверка job_embeddings
if [ "${EMB_COUNT:-0}" -lt 1 ]; then
  echo "ERROR: job_embeddings не создан для job_id=$JOB_ID"
  echo "Проверьте логи: docker compose logs ai-service"
  exit 1
else
  echo "✓ job_embeddings создан"
fi

# Push в match-notify (имитация matching)
docker compose exec -T redis redis-cli LPUSH match-notify "{\"user_id\":$USER_ID,\"job_id\":$JOB_ID,\"match_score\":0.85}" >/dev/null 2>&1 || \
  redis-cli -u "$REDIS_URL" LPUSH match-notify "{\"user_id\":$USER_ID,\"job_id\":$JOB_ID,\"match_score\":0.85}" >/dev/null 2>&1
echo "✓ Сообщение в match-notify"

echo ""
echo "=== 9.9: Уведомление в Telegram ==="
echo "Notifier обработает очередь. Проверьте Telegram (chat_id=$TELEGRAM_ID) или логи:"
echo "  docker compose logs backend-notifier"
echo ""
echo "E2E завершён. Для остановки: docker compose down"
