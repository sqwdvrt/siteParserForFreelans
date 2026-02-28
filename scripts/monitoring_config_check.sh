#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[monitoring] promtool check config"
docker run --rm \
  --entrypoint promtool \
  -v "${ROOT_DIR}/monitoring/prometheus:/etc/prometheus:ro" \
  prom/prometheus:v2.54.1 \
  check config /etc/prometheus/prometheus.yml

echo "[monitoring] promtool check rules"
docker run --rm \
  --entrypoint promtool \
  -v "${ROOT_DIR}/monitoring/prometheus:/etc/prometheus:ro" \
  prom/prometheus:v2.54.1 \
  check rules /etc/prometheus/alerts.yml

echo "[monitoring] amtool check config"
ALERTMANAGER_TELEGRAM_CHAT_ID="${ALERTMANAGER_TELEGRAM_CHAT_ID:-123456789}" \
  sh "${ROOT_DIR}/monitoring/alertmanager/render_config.sh" \
  "${ROOT_DIR}/monitoring/alertmanager/alertmanager.yml.tmpl" \
  "${TMP_DIR}/alertmanager.yml"

docker run --rm \
  --entrypoint amtool \
  -v "${TMP_DIR}:/tmp/alertmanager:ro" \
  prom/alertmanager:v0.27.0 \
  check-config /tmp/alertmanager/alertmanager.yml

echo "[monitoring] docker compose check"
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml config >/dev/null

echo "[monitoring] configuration checks passed"
