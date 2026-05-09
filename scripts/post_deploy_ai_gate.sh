#!/usr/bin/env bash
set -euo pipefail

POST_DEPLOY_HEALTH_TIMEOUT_SEC="${POST_DEPLOY_HEALTH_TIMEOUT_SEC:-300}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"

if ! [[ "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}" =~ ^[0-9]+$ ]] || [[ "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}" -le 0 ]]; then
  echo "ERROR: POST_DEPLOY_HEALTH_TIMEOUT_SEC must be a positive integer, got: ${POST_DEPLOY_HEALTH_TIMEOUT_SEC}" >&2
  exit 1
fi

for bin in docker curl jq; do
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: ${bin}" >&2
    exit 1
  fi
done

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

check_prometheus_targets() {
  local timeout_sec="$1"
  local elapsed=0

  while [[ "${elapsed}" -lt "${timeout_sec}" ]]; do
    local targets_json
    if targets_json="$(env -u SSL_CERT_FILE -u CURL_CA_BUNDLE -u REQUESTS_CA_BUNDLE curl --silent --show-error --fail "${PROMETHEUS_URL}/api/v1/targets" 2>/dev/null)"; then
      if printf '%s' "${targets_json}" | jq -e '
        ["ai-service", "ai-ac-consumer", "ai-user-embed", "ai-user-rematch"] as $required
        | [.data.activeTargets[] | select(.labels.job as $job | $required | index($job)) | select(.health == "up") | .labels.job] as $up
        | ($required - $up) as $missing
        | if ($missing | length) == 0 then true else false end
      ' >/dev/null; then
        echo "OK: Prometheus AI targets are up"
        return 0
      fi
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done

  local targets_json
  targets_json="$(env -u SSL_CERT_FILE -u CURL_CA_BUNDLE -u REQUESTS_CA_BUNDLE curl --silent --show-error --fail "${PROMETHEUS_URL}/api/v1/targets")"
  printf '%s' "${targets_json}" | jq -e '
    ["ai-service", "ai-ac-consumer", "ai-user-embed", "ai-user-rematch"] as $required
    | [.data.activeTargets[] | select(.labels.job as $job | $required | index($job)) | select(.health == "up") | .labels.job] as $up
    | ($required - $up) as $missing
    | if ($missing | length) == 0 then true else error("missing healthy Prometheus AI targets: " + ($missing | join(", "))) end
  ' >/dev/null
  echo "OK: Prometheus AI targets are up"
}

echo "=== AI post-deploy compose health gate ==="
wait_for_compose_health "ai-service" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "ai-user-embed" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "ai-user-rematch" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"
wait_for_compose_health "ai-ac-consumer" "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"

echo "=== AI post-deploy Prometheus target gate ==="
check_prometheus_targets "${POST_DEPLOY_HEALTH_TIMEOUT_SEC}"

echo "[post-deploy-ai-gate] All checks passed."
