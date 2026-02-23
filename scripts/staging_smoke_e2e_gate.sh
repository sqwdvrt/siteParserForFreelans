#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-}"
AUTH_TOKEN="${API_AUTH_TOKEN:-}"
USER_HMAC_SECRET="${API_USER_HMAC_SECRET:-}"
TELEGRAM_ID="${E2E_TELEGRAM_ID:-${TELEGRAM_ID:-123456789}}"

if [[ -z "$API_URL" ]]; then
  echo "ERROR: API_URL is required (example: https://staging-api.example.com)"
  exit 1
fi
if [[ -z "$AUTH_TOKEN" ]]; then
  echo "ERROR: API_AUTH_TOKEN is required"
  exit 1
fi
if [[ -z "$USER_HMAC_SECRET" ]]; then
  echo "ERROR: API_USER_HMAC_SECRET is required"
  exit 1
fi
if ! [[ "$TELEGRAM_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: E2E_TELEGRAM_ID/TELEGRAM_ID must be numeric, got: $TELEGRAM_ID"
  exit 1
fi

for bin in curl openssl xxd sed awk; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: $bin"
    exit 1
  fi
done
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: required command is missing: docker"
  exit 1
fi

API_URL="${API_URL%/}"
AI_RUNTIME_GATE_COLD_TIMEOUT_SEC="${AI_RUNTIME_GATE_COLD_TIMEOUT_SEC:-360}"
AI_RUNTIME_GATE_WARM_TIMEOUT_SEC="${AI_RUNTIME_GATE_WARM_TIMEOUT_SEC:-180}"
AI_RUNTIME_GATE_POLL_INTERVAL_SEC="${AI_RUNTIME_GATE_POLL_INTERVAL_SEC:-5}"
AI_RUNTIME_GATE_READY_TIMEOUT_SEC="${AI_RUNTIME_GATE_READY_TIMEOUT_SEC:-300}"

if ! [[ "$AI_RUNTIME_GATE_COLD_TIMEOUT_SEC" =~ ^[0-9]+$ ]] || [[ "$AI_RUNTIME_GATE_COLD_TIMEOUT_SEC" -le 0 ]]; then
  echo "ERROR: AI_RUNTIME_GATE_COLD_TIMEOUT_SEC must be a positive integer, got: $AI_RUNTIME_GATE_COLD_TIMEOUT_SEC"
  exit 1
fi
if ! [[ "$AI_RUNTIME_GATE_WARM_TIMEOUT_SEC" =~ ^[0-9]+$ ]] || [[ "$AI_RUNTIME_GATE_WARM_TIMEOUT_SEC" -le 0 ]]; then
  echo "ERROR: AI_RUNTIME_GATE_WARM_TIMEOUT_SEC must be a positive integer, got: $AI_RUNTIME_GATE_WARM_TIMEOUT_SEC"
  exit 1
fi
if ! [[ "$AI_RUNTIME_GATE_POLL_INTERVAL_SEC" =~ ^[0-9]+$ ]] || [[ "$AI_RUNTIME_GATE_POLL_INTERVAL_SEC" -le 0 ]]; then
  echo "ERROR: AI_RUNTIME_GATE_POLL_INTERVAL_SEC must be a positive integer, got: $AI_RUNTIME_GATE_POLL_INTERVAL_SEC"
  exit 1
fi
if ! [[ "$AI_RUNTIME_GATE_READY_TIMEOUT_SEC" =~ ^[0-9]+$ ]] || [[ "$AI_RUNTIME_GATE_READY_TIMEOUT_SEC" -le 0 ]]; then
  echo "ERROR: AI_RUNTIME_GATE_READY_TIMEOUT_SEC must be a positive integer, got: $AI_RUNTIME_GATE_READY_TIMEOUT_SEC"
  exit 1
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

compose_prod() {
  docker compose --env-file .env.production -f docker-compose.prod.yml "$@"
}

detect_ai_python_bin() {
  local py_bin
  if ! py_bin="$(
    compose_prod exec -T ai-service sh -lc '
      if command -v python3 >/dev/null 2>&1; then
        echo python3
      elif command -v python >/dev/null 2>&1; then
        echo python
      fi
    ' | tr -d '\r'
  )"; then
    return 1
  fi
  if [[ -z "$py_bin" ]]; then
    return 1
  fi
  printf '%s\n' "$py_bin"
}

wait_ai_ready() {
  local ready_timeout="$1"
  local started_at
  started_at="$(date +%s)"
  while true; do
    if compose_prod exec -T ai-service sh -lc 'test -f "${AI_READY_FILE:-/tmp/ai-consumer-ready}"'; then
      return 0
    fi
    if (( "$(date +%s)" - started_at >= ready_timeout )); then
      return 1
    fi
    sleep 5
  done
}

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

http_code() {
  local url="$1"
  local out_file="$2"
  curl -sS -o "$out_file" -w "%{http_code}" "$url"
}

echo "=== Smoke: healthz/readyz ==="
health_code="$(http_code "${API_URL}/healthz" "${tmp_dir}/healthz.txt")"
ready_code="$(http_code "${API_URL}/readyz" "${tmp_dir}/readyz.txt")"
if [[ "$health_code" != "200" ]]; then
  echo "ERROR: /healthz returned ${health_code}"
  cat "${tmp_dir}/healthz.txt"
  exit 1
fi
if [[ "$ready_code" != "200" ]]; then
  echo "ERROR: /readyz returned ${ready_code}"
  cat "${tmp_dir}/readyz.txt"
  exit 1
fi
echo "OK: /healthz=${health_code}, /readyz=${ready_code}"

echo "=== E2E: POST /users ==="
post_body="{\"telegram_id\":${TELEGRAM_ID}}"
post_ts="$(date +%s)"
post_nonce="$(generate_nonce)"
post_sig="$(sign_user_request "POST" "/users" "$TELEGRAM_ID" "$post_ts" "$post_nonce" "$post_body")"
post_resp_file="${tmp_dir}/post_users_body.txt"
post_code="$(
  curl -sS -o "$post_resp_file" -w "%{http_code}" -X POST "${API_URL}/users" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Telegram-ID: ${TELEGRAM_ID}" \
    -H "X-Request-Timestamp: ${post_ts}" \
    -H "X-Request-Nonce: ${post_nonce}" \
    -H "X-Request-Signature: ${post_sig}" \
    -H "Content-Type: application/json" \
    -d "$post_body"
)"
if [[ "$post_code" != "200" ]]; then
  echo "ERROR: POST /users returned ${post_code}"
  cat "$post_resp_file"
  exit 1
fi
user_id="$(sed -n 's/.*"user_id":[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$post_resp_file" | head -n1)"
if [[ -z "$user_id" ]]; then
  echo "ERROR: failed to parse user_id from POST /users response"
  cat "$post_resp_file"
  exit 1
fi
echo "OK: user_id=${user_id}"

echo "=== E2E: PUT /users/${user_id}/profile ==="
profile_text="staging-gate $(date -u +%Y-%m-%dT%H:%M:%SZ)"
put_body="$(printf '{"profile_text":"%s"}' "$profile_text")"
put_path="/users/${user_id}/profile"
put_ts="$(date +%s)"
put_nonce="$(generate_nonce)"
put_sig="$(sign_user_request "PUT" "$put_path" "$TELEGRAM_ID" "$put_ts" "$put_nonce" "$put_body")"
put_code="$(
  curl -sS -o "${tmp_dir}/put_profile_body.txt" -w "%{http_code}" -X PUT "${API_URL}${put_path}" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Telegram-ID: ${TELEGRAM_ID}" \
    -H "X-Request-Timestamp: ${put_ts}" \
    -H "X-Request-Nonce: ${put_nonce}" \
    -H "X-Request-Signature: ${put_sig}" \
    -H "Content-Type: application/json" \
    -d "$put_body"
)"
if [[ "$put_code" != "204" ]]; then
  echo "ERROR: PUT ${put_path} returned ${put_code}"
  cat "${tmp_dir}/put_profile_body.txt"
  exit 1
fi
echo "OK: profile updated"

echo "=== Runtime gate: ai-process cold/warm runs ==="
if ! ai_python_bin="$(detect_ai_python_bin)"; then
  echo "ERROR: failed to detect python/python3 in ai-service container"
  compose_prod logs --tail=200 ai-service || true
  exit 1
fi
echo "runtime-gate: using interpreter '${ai_python_bin}' in ai-service"

if ! wait_ai_ready "$AI_RUNTIME_GATE_READY_TIMEOUT_SEC"; then
  echo "ERROR: ai-service did not become ready within ${AI_RUNTIME_GATE_READY_TIMEOUT_SEC}s"
  compose_prod logs --tail=200 ai-service || true
  exit 1
fi
echo "runtime-gate: ai-service ready file detected"

if compose_prod exec -T \
  -e AI_RUNTIME_GATE_COLD_TIMEOUT_SEC="$AI_RUNTIME_GATE_COLD_TIMEOUT_SEC" \
  -e AI_RUNTIME_GATE_WARM_TIMEOUT_SEC="$AI_RUNTIME_GATE_WARM_TIMEOUT_SEC" \
  -e AI_RUNTIME_GATE_POLL_INTERVAL_SEC="$AI_RUNTIME_GATE_POLL_INTERVAL_SEC" \
  ai-service "$ai_python_bin" - <<'PY'
import json
import os
import sys
import time
import traceback
import uuid

import psycopg2
import redis


def fail(message: str, *, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def parse_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        fail(f"{name} must be an integer, got: {raw!r}")
    if value <= 0:
        fail(f"{name} must be > 0, got: {value}")
    return value


def payload_has_job(raw: str, job_id: int) -> bool:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    try:
        current_id = int(data.get("job_id"))
    except (TypeError, ValueError):
        return False
    return current_id == job_id


def queue_contains_job(client: redis.Redis, queue_name: str, job_id: int) -> bool:
    for payload in client.lrange(queue_name, 0, -1):
        if payload_has_job(payload, job_id):
            return True
    return False


def job_embeddings_count(cursor, job_id: int) -> int:
    cursor.execute("SELECT COUNT(*) FROM job_embeddings WHERE job_id = %s", (job_id,))
    row = cursor.fetchone()
    if row is None:
        return 0
    return int(row[0])


def main() -> None:
    cold_timeout_sec = parse_positive_int("AI_RUNTIME_GATE_COLD_TIMEOUT_SEC", 360)
    warm_timeout_sec = parse_positive_int("AI_RUNTIME_GATE_WARM_TIMEOUT_SEC", 180)
    poll_interval_sec = parse_positive_int("AI_RUNTIME_GATE_POLL_INTERVAL_SEC", 5)

    db_url = os.getenv("DATABASE_URL")
    redis_url = os.getenv("REDIS_URL")
    if not db_url:
        fail("DATABASE_URL is not set in ai-service environment")
    if not redis_url:
        fail("REDIS_URL is not set in ai-service environment")

    queue_name = os.getenv("AI_QUEUE", "ai-process")
    processing_queue = f"{queue_name}:processing"
    dlq_queue = f"{queue_name}:dlq"

    redis_client = redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    db_conn = psycopg2.connect(db_url)
    db_conn.autocommit = True
    db_cursor = db_conn.cursor()

    def run_probe(label: str, timeout_sec: int) -> None:
        marker = f"staging-runtime-gate-{label}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        db_cursor.execute(
            """
            INSERT INTO jobs (source, url, title, description, raw_html)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "runtime-gate",
                f"https://runtime-gate.local/jobs/{marker}",
                f"Runtime gate probe {marker}",
                f"Runtime gate {label} probe to verify ai-process queue drain",
                "<p>runtime-gate-probe</p>",
            ),
        )
        row = db_cursor.fetchone()
        if row is None:
            fail(f"failed to create runtime-gate job in DB for {label}")
        job_id = int(row[0])

        dlq_before = redis_client.llen(dlq_queue)
        redis_client.lpush(queue_name, json.dumps({"job_id": job_id}, separators=(",", ":")))
        print(
            f"runtime-gate[{label}]: enqueued job_id={job_id} "
            f"queue={queue_name} dlq_before={dlq_before}",
            flush=True,
        )

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            embeddings = job_embeddings_count(db_cursor, job_id)
            main_len = redis_client.llen(queue_name)
            processing_len = redis_client.llen(processing_queue)
            dlq_len = redis_client.llen(dlq_queue)
            in_main = queue_contains_job(redis_client, queue_name, job_id)
            in_processing = queue_contains_job(redis_client, processing_queue, job_id)
            in_dlq = queue_contains_job(redis_client, dlq_queue, job_id)

            print(
                f"runtime-gate[{label}]: "
                f"job_id={job_id} embeddings={embeddings} "
                f"in_main={in_main} in_processing={in_processing} in_dlq={in_dlq} "
                f"queue_len={main_len} processing_len={processing_len} dlq_len={dlq_len}",
                flush=True,
            )

            if in_dlq:
                fail(f"runtime-gate[{label}] failed: job_id={job_id} moved to {dlq_queue}")
            if dlq_len > dlq_before:
                fail(
                    f"runtime-gate[{label}] failed: DLQ grew "
                    f"({dlq_before} -> {dlq_len}) while processing job_id={job_id}"
                )
            if embeddings >= 1 and not in_main and not in_processing:
                print(
                    f"runtime-gate[{label}]: success job_id={job_id} processed and queue cleared",
                    flush=True,
                )
                return

            time.sleep(poll_interval_sec)

        embeddings = job_embeddings_count(db_cursor, job_id)
        in_main = queue_contains_job(redis_client, queue_name, job_id)
        in_processing = queue_contains_job(redis_client, processing_queue, job_id)
        in_dlq = queue_contains_job(redis_client, dlq_queue, job_id)
        main_len = redis_client.llen(queue_name)
        processing_len = redis_client.llen(processing_queue)
        dlq_len = redis_client.llen(dlq_queue)
        fail(
            f"runtime-gate[{label}] timed out waiting for ai-process drain: "
            f"job_id={job_id} embeddings={embeddings} "
            f"in_main={in_main} in_processing={in_processing} in_dlq={in_dlq} "
            f"queue_len={main_len} processing_len={processing_len} dlq_len={dlq_len}",
        )

    run_probe("cold-start", cold_timeout_sec)
    run_probe("warm", warm_timeout_sec)
    print("runtime-gate: cold-start and warm probes passed", flush=True)
    db_cursor.close()
    db_conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exception(exc, file=sys.stderr)
        fail(f"unexpected runtime-gate failure: {exc}")
PY
then
  echo "OK: ai-process runtime gate passed"
else
  echo "ERROR: ai-process runtime gate failed"
  compose_prod logs --tail=200 ai-service || true
  exit 1
fi

echo "=== Gate PASSED ==="
