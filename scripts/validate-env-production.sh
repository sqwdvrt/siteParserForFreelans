#!/usr/bin/env bash
# Validates .env.production before a production or staging deploy.
# Run on the deployment host where .env.production lives.
#
# Usage: bash scripts/validate-env-production.sh [path/to/.env.production]
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed (details printed to stderr)

set -euo pipefail

ENV_FILE="${1:-.env.production}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
errors=0

_fail() {
  echo "  FAIL  $*" >&2
  errors=$((errors + 1))
}

# Extract value for KEY from the env file.
# Handles KEY=value, KEY="quoted", KEY='single'. Returns empty string if not found.
_get() {
  local name="$1"
  local raw val
  # Take the last matching line (in case of duplicates), skip comments
  raw="$(grep -E "^${name}=" "$ENV_FILE" | grep -v "^#" | tail -n1)" || true
  if [[ -z "$raw" ]]; then
    printf ''
    return
  fi
  # Everything after the first '='
  val="${raw#*=}"
  # Strip surrounding double-quotes
  if [[ "$val" =~ ^\"(.*)\"$ ]]; then val="${BASH_REMATCH[1]}"; fi
  # Strip surrounding single-quotes
  if [[ "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
  printf '%s' "$val"
}

# Mirrors Go's forbiddenSecretPrefixes in backend/internal/security/secrets.go
_is_placeholder() {
  local val lower
  val="$1"
  lower="$(printf '%s' "$val" | tr '[:upper:]' '[:lower:]')"
  local prefix
  for prefix in change_me changeme replace_me replace_with your_ example_ dummy_ test_; do
    case "$lower" in
      "${prefix}"*) return 0 ;;
    esac
  done
  # Additional value-level patterns not covered by prefix rules
  case "$lower" in
    *example.com*|*your-ollama-host*|*replace/me*|*placeholder*) return 0 ;;
  esac
  return 1
}

check_required() {
  local name="$1"
  local val
  val="$(_get "$name")"
  if [[ -z "$val" ]]; then
    _fail "$name: not set"
    return
  fi
  if _is_placeholder "$val"; then
    _fail "$name: contains a placeholder value"
  fi
}

check_min_length() {
  local name="$1" min="$2"
  local val
  val="$(_get "$name")"
  if [[ "${#val}" -lt "$min" ]]; then
    _fail "$name: must be at least ${min} characters (got ${#val})"
  fi
}

check_prefix() {
  local name="$1" prefix="$2"
  local val
  val="$(_get "$name")"
  case "$val" in
    "${prefix}"*) ;;
    *) _fail "$name: must start with '${prefix}' (got: ${val:0:40})" ;;
  esac
}

check_pattern() {
  local name="$1" pattern="$2" description="$3"
  local val
  val="$(_get "$name")"
  if ! printf '%s' "$val" | grep -qE "$pattern"; then
    _fail "$name: $description"
  fi
}

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
echo "Validating $ENV_FILE ..."
echo ""

# APP_ENV must be "production"
check_required APP_ENV
if [[ "$(_get APP_ENV)" != "production" ]]; then
  _fail "APP_ENV: must be 'production' (got: $(_get APP_ENV))"
fi

# DATABASE_URL: postgres(ql)://, strong password, sslmode=require|verify-*
check_required DATABASE_URL
check_prefix   DATABASE_URL "postgres"
check_pattern  DATABASE_URL \
  "sslmode=(require|verify-ca|verify-full)" \
  "must include sslmode=require, sslmode=verify-ca, or sslmode=verify-full"

# REDIS_URL: rediss:// (TLS required in prod), must carry credentials
check_required REDIS_URL
check_prefix   REDIS_URL "rediss://"
case "$(_get REDIS_URL)" in
  *:*@*) ;;
  *) _fail "REDIS_URL: must include password credentials (rediss://user:password@host)" ;;
esac

# API secrets — minimum 24 chars (Go default ValidateSecret minLen)
check_required   API_AUTH_TOKEN
check_min_length API_AUTH_TOKEN 24
check_required   API_USER_HMAC_SECRET
check_min_length API_USER_HMAC_SECRET 24

# TLS certificate host paths — required by docker-compose.prod.yml :? operator
check_required API_TLS_CERT_HOST_PATH
check_required API_TLS_KEY_HOST_PATH

# Telegram bot token
check_required   TELEGRAM_BOT_TOKEN
check_min_length TELEGRAM_BOT_TOKEN 20

# API_URL must be HTTPS with a non-empty host
check_required API_URL
check_prefix   API_URL "https://"
check_pattern  API_URL "^https://[^/]" "must include a non-empty host after https://"

# OLLAMA_URL — required; must not be the example placeholder
check_required OLLAMA_URL
case "$(_get OLLAMA_URL)" in
  *your-ollama-host*) _fail "OLLAMA_URL: contains placeholder 'your-ollama-host'" ;;
esac

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
echo ""
if [[ "$errors" -gt 0 ]]; then
  echo "FAILED: ${errors} error(s) in ${ENV_FILE} — deploy aborted." >&2
  exit 1
fi

echo "OK: ${ENV_FILE} passed all checks."
