#!/usr/bin/env bash

RELEASE_SMOKE_PAYLOADS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_SMOKE_PROFILE_FILE="${RELEASE_SMOKE_PROFILE_FILE:-${RELEASE_SMOKE_PAYLOADS_DIR}/../testdata/release_smoke_profile.txt}"

load_release_smoke_profile_text() {
  if [[ ! -f "$RELEASE_SMOKE_PROFILE_FILE" ]]; then
    echo "ERROR: release smoke profile payload file not found: $RELEASE_SMOKE_PROFILE_FILE" >&2
    return 1
  fi
  local profile_text
  profile_text="$(<"$RELEASE_SMOKE_PROFILE_FILE")"
  if [[ -z "$profile_text" ]]; then
    echo "ERROR: release smoke profile payload is empty: $RELEASE_SMOKE_PROFILE_FILE" >&2
    return 1
  fi
  printf '%s' "$profile_text"
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/ }"
  value="${value//$'\r'/ }"
  printf '%s' "$value"
}

build_release_smoke_profile_body() {
  local profile_text="$1"
  printf '{"profile_text":"%s"}' "$(json_escape "$profile_text")"
}
