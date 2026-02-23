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

API_URL="${API_URL%/}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

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

echo "=== Gate PASSED ==="
