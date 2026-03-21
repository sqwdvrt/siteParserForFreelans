#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="${ROOT_DIR}/browser-service/testdata/render-smoke"
IMAGE_TAG="${BROWSER_SERVICE_TEST_IMAGE:-site-parser-browser-service:test}"
HTTP_SERVER_PORT="${BROWSER_SMOKE_FIXTURE_PORT:-}"
BROWSER_SERVICE_PORT="${BROWSER_SMOKE_SERVICE_PORT:-}"
TEST_HOST_ALLOWLIST="${BROWSER_SERVICE_TEST_HOST_ALLOWLIST:-host.docker.internal}"
CONTAINER_NAME="browser-service-smoke-$$"
HTTP_SERVER_PID=""
BODY_FILE=""

require_bin() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: $1" >&2
    exit 1
  fi
}

pick_port() {
  python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

cleanup() {
  if [[ -n "${BODY_FILE}" ]]; then
    rm -f "${BODY_FILE}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${HTTP_SERVER_PID}" ]]; then
    kill "${HTTP_SERVER_PID}" >/dev/null 2>&1 || true
    wait "${HTTP_SERVER_PID}" >/dev/null 2>&1 || true
  fi
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

require_bin docker
require_bin curl
require_bin python3

if [[ ! -f "${FIXTURE_DIR}/index.html" ]]; then
  echo "ERROR: fixture not found: ${FIXTURE_DIR}/index.html" >&2
  exit 1
fi

HTTP_SERVER_PORT="${HTTP_SERVER_PORT:-$(pick_port)}"
BROWSER_SERVICE_PORT="${BROWSER_SERVICE_PORT:-$(pick_port)}"

echo "[browser-smoke] serving fixture from ${FIXTURE_DIR} on 0.0.0.0:${HTTP_SERVER_PORT}"
(
  cd "${FIXTURE_DIR}"
  # The browser-service container reaches the fixture through the host-gateway
  # mapping, so the host-side HTTP server must listen beyond loopback.
  exec python3 -m http.server "${HTTP_SERVER_PORT}" --bind 0.0.0.0
) >/tmp/browser-service-fixture.log 2>&1 &
HTTP_SERVER_PID=$!

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${HTTP_SERVER_PORT}/index.html" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if ! curl -fsS "http://127.0.0.1:${HTTP_SERVER_PORT}/index.html" >/dev/null 2>&1; then
  echo "ERROR: fixture HTTP server did not become ready" >&2
  exit 1
fi

echo "[browser-smoke] building browser-service image ${IMAGE_TAG}"
docker build -t "${IMAGE_TAG}" "${ROOT_DIR}/browser-service"

run_container() {
  docker run -d --rm \
    --name "${CONTAINER_NAME}" \
    -e "BROWSER_SERVICE_TEST_HOST_ALLOWLIST=${TEST_HOST_ALLOWLIST}" \
    --add-host host.docker.internal:host-gateway \
    -p "127.0.0.1:${BROWSER_SERVICE_PORT}:8090" \
    "${IMAGE_TAG}"
}

if ! run_container >/dev/null; then
  echo "[browser-smoke] docker host-gateway mapping failed, retrying without explicit add-host"
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker run -d --rm \
    --name "${CONTAINER_NAME}" \
    -e "BROWSER_SERVICE_TEST_HOST_ALLOWLIST=${TEST_HOST_ALLOWLIST}" \
    -p "127.0.0.1:${BROWSER_SERVICE_PORT}:8090" \
    "${IMAGE_TAG}" >/dev/null
fi

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${BROWSER_SERVICE_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:${BROWSER_SERVICE_PORT}/healthz" >/dev/null 2>&1; then
  echo "ERROR: browser-service /healthz did not become ready" >&2
  docker logs "${CONTAINER_NAME}" || true
  exit 1
fi

TARGET_URL="http://host.docker.internal:${HTTP_SERVER_PORT}/index.html"
RENDER_URL="$(
  python3 - <<'PY' "${BROWSER_SERVICE_PORT}" "${TARGET_URL}"
from urllib.parse import quote
import sys

port = sys.argv[1]
target_url = sys.argv[2]
endpoint = f"http://127.0.0.1:{port}/render?url={quote(target_url, safe='')}"
print(endpoint)
PY
)"

BODY_FILE="$(mktemp)"
HTTP_CODE="$(curl -sS -o "${BODY_FILE}" -w "%{http_code}" "${RENDER_URL}")"
if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "ERROR: browser-service /render returned HTTP ${HTTP_CODE}" >&2
  cat "${BODY_FILE}" >&2
  docker logs "${CONTAINER_NAME}" || true
  exit 1
fi

python3 - <<'PY' "${BODY_FILE}" "${TARGET_URL}"
import json
from pathlib import Path
import sys

body_path = Path(sys.argv[1])
target_url = sys.argv[2]
payload = json.loads(body_path.read_text(encoding="utf-8"))
html = payload.get("html", "")
if payload.get("url") != target_url:
    raise SystemExit(f"unexpected url in response: {payload.get('url')!r}")
if "want-card" not in html:
    raise SystemExit("rendered HTML does not contain want-card marker")
if "rendered via javascript" not in html:
    raise SystemExit("rendered HTML does not contain the JS-rendered content marker")
print("browser-service integration smoke passed")
PY
