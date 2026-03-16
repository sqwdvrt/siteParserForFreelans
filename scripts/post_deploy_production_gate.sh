#!/usr/bin/env bash
set -euo pipefail

API_PORT="${API_PORT:-8443}"
WEBHOOK_URL="${WEBHOOK_URL:-}"
WEBHOOK_SECRET_TOKEN="${WEBHOOK_SECRET_TOKEN:-}"
POST_DEPLOY_HEALTH_TIMEOUT_SEC="${POST_DEPLOY_HEALTH_TIMEOUT_SEC:-240}"
POST_DEPLOY_HTTP_TIMEOUT_SEC="${POST_DEPLOY_HTTP_TIMEOUT_SEC:-120}"

if ! [[ "${API_PORT}" =~ ^[0-9]+$ ]] || [[ "${API_PORT}" -le 0 ]]; then
  echo "ERROR: API_PORT must be a positive integer, got: ${API_PORT}" >&2
  exit 1
fi
if ! [[ "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}" =~ ^[0-9]+$ ]] || [[ "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}" -le 0 ]]; then
  echo "ERROR: POST_DEPLOY_HEALTH_TIMEOUT_SEC must be a positive integer, got: ${POST_DEPLOY_HEALTH_TIMEOUT_SEC}" >&2
  exit 1
fi
if ! [[ "${POST_DEPLOY_HTTP_TIMEOUT_SEC}" =~ ^[0-9]+$ ]] || [[ "${POST_DEPLOY_HTTP_TIMEOUT_SEC}" -le 0 ]]; then
  echo "ERROR: POST_DEPLOY_HTTP_TIMEOUT_SEC must be a positive integer, got: ${POST_DEPLOY_HTTP_TIMEOUT_SEC}" >&2
  exit 1
fi
if [[ -z "${WEBHOOK_URL}" ]]; then
  echo "ERROR: WEBHOOK_URL is required" >&2
  exit 1
fi
if [[ -z "${WEBHOOK_SECRET_TOKEN}" ]]; then
  echo "ERROR: WEBHOOK_SECRET_TOKEN is required" >&2
  exit 1
fi

for bin in docker curl mktemp; do
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: ${bin}" >&2
    exit 1
  fi
done

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

compose_prod() {
  docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.ssl.yml "$@"
}

wait_for_compose_health() {
  local service="$1"
  local timeout_sec="$2"
  local elapsed=0
  local container_id
  container_id="$(compose_prod ps -q "${service}" | head -n1)"

  if [[ -z "${container_id}" ]]; then
    echo "ERROR: container for service '${service}' not found" >&2
    return 1
  fi

  echo "Waiting for compose health: ${service} (${timeout_sec}s)"
  while [[ "${elapsed}" -lt "${timeout_sec}" ]]; do
    local status
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${container_id}" 2>/dev/null || echo unknown)"
    if [[ "${status}" = "healthy" ]]; then
      echo "OK: ${service} healthy"
      return 0
    fi
    if [[ "${status}" = "unhealthy" ]]; then
      echo "ERROR: ${service} is unhealthy" >&2
      compose_prod logs --tail=160 "${service}" || true
      return 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  echo "ERROR: timeout waiting for ${service} to become healthy" >&2
  compose_prod logs --tail=160 "${service}" || true
  return 1
}

expect_http_code() {
  local name="$1"
  local method="$2"
  local url="$3"
  local expected_code="$4"
  local timeout_sec="$5"
  shift 5

  local started_at
  started_at="$(date +%s)"
  while true; do
    local out_file status
    out_file="${tmp_dir}/$(printf '%s' "${name}" | tr ' /' '__').txt"
    status="$(
      curl --silent --show-error --output "${out_file}" --write-out '%{http_code}' \
        --request "${method}" \
        --max-time 15 \
        "$@" \
        "${url}" || true
    )"
    if [[ "${status}" = "${expected_code}" ]]; then
      echo "OK: ${name} -> ${status}"
      return 0
    fi
    if (( "$(date +%s)" - started_at >= timeout_sec )); then
      echo "ERROR: ${name} returned ${status}, expected ${expected_code}" >&2
      if [[ -s "${out_file}" ]]; then
        cat "${out_file}" >&2
      fi
      return 1
    fi
    sleep 3
  done
}

echo "=== Production post-deploy compose health gate ==="
wait_for_compose_health "backend-api" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "browser-service" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "backend-crawler" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "backend-notifier" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "ai-service" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "ai-user-embed" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "ai-user-rematch" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "ai-ac-consumer" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "telegram-bot" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"

echo "=== Production post-deploy local endpoint gate ==="
expect_http_code "local api healthz" "GET" "https://127.0.0.1:${API_PORT}/healthz" "200" "${POST_DEPLOY_HTTP_TIMEOUT_SEC}" --insecure
expect_http_code "local api readyz" "GET" "https://127.0.0.1:${API_PORT}/readyz" "200" "${POST_DEPLOY_HTTP_TIMEOUT_SEC}" --insecure

echo "=== Production post-deploy webhook ingress gate ==="
expect_http_code \
  "public webhook ingress" \
  "POST" \
  "${WEBHOOK_URL}" \
  "400" \
  "${POST_DEPLOY_HTTP_TIMEOUT_SEC}" \
  --header "X-Telegram-Bot-Api-Secret-Token: ${WEBHOOK_SECRET_TOKEN}" \
  --header "Content-Type: application/json" \
  --data ''

echo "[post-deploy-production-gate] All checks passed."
