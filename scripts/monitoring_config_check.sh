#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
TMP_DIR="$(mktemp -d)"
# Docker containers reading bind-mounted files need execute permission on the
# parent temp directory to traverse it on the host filesystem.
chmod 755 "$TMP_DIR"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "[monitoring] promtool check config"
BACKEND_API_METRICS_TARGET="${BACKEND_API_METRICS_TARGET:-backend-api:8080}" \
BACKEND_API_METRICS_SCHEME="${BACKEND_API_METRICS_SCHEME:-http}" \
BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY="${BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY:-false}" \
  sh "${ROOT_DIR}/monitoring/prometheus/render_config.sh" \
  "${ROOT_DIR}/monitoring/prometheus/prometheus.yml.tmpl" \
  "${TMP_DIR}/prometheus.yml"

docker run --rm \
  --entrypoint promtool \
  -v "${ROOT_DIR}/monitoring/prometheus/recording_rules.yml:/etc/prometheus/recording_rules.yml:ro" \
  -v "${ROOT_DIR}/monitoring/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro" \
  -v "${TMP_DIR}:/tmp/prometheus:ro" \
  prom/prometheus:v2.54.1 \
  check config /tmp/prometheus/prometheus.yml

echo "[monitoring] promtool check rules"
docker run --rm \
  --entrypoint promtool \
  -v "${ROOT_DIR}/monitoring/prometheus:/etc/prometheus:ro" \
  prom/prometheus:v2.54.1 \
  check rules /etc/prometheus/alerts.yml

echo "[monitoring] amtool check config"
ALERTMANAGER_TELEGRAM_BOT_TOKEN="${ALERTMANAGER_TELEGRAM_BOT_TOKEN:-dummy-token-for-config-check}" \
ALERTMANAGER_TELEGRAM_CHAT_ID="${ALERTMANAGER_TELEGRAM_CHAT_ID:-123456789}" \
ALERTMANAGER_SLACK_WEBHOOK_URL="${ALERTMANAGER_SLACK_WEBHOOK_URL:-}" \
  sh "${ROOT_DIR}/monitoring/alertmanager/render_config.sh" \
  "${ROOT_DIR}/monitoring/alertmanager/alertmanager.yml.tmpl" \
  "${TMP_DIR}/alertmanager.yml"

docker run --rm \
  --entrypoint amtool \
  -v "${TMP_DIR}:/tmp/alertmanager:ro" \
  prom/alertmanager:v0.27.0 \
  check-config /tmp/alertmanager/alertmanager.yml

echo "[monitoring] docker compose check (production stack)"
docker compose --env-file .env.production.example \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl.yml \
  -f docker-compose.monitoring.yml \
  --profile monitoring \
  config >/dev/null

echo "[monitoring] configuration checks passed"
