#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

GO_TOOLCHAIN="${GO_TOOLCHAIN:-go1.25.8}"
GO_GOMODCACHE="${GO_GOMODCACHE:-${ROOT_DIR}/backend/.gomodcache-${GO_TOOLCHAIN}}"
GO_GOCACHE="${GO_GOCACHE:-${ROOT_DIR}/backend/.gocache-${GO_TOOLCHAIN}}"
PYTHON_BIN="${PYTHON_BIN:-}"
PREPARED_PROD_ENV=0

cleanup() {
  if [[ "${PREPARED_PROD_ENV}" == "1" ]]; then
    rm -f "${ROOT_DIR}/.env.production"
  fi
}
trap cleanup EXIT

run_step() {
  local name="$1"
  shift
  echo ""
  echo "=== [release-preflight] ${name} ==="
  "$@"
}

resolve_python_bin() {
  if [[ -n "${PYTHON_BIN}" ]]; then
    if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
      printf '%s\n' "${PYTHON_BIN}"
      return 0
    fi
    echo "ERROR: PYTHON_BIN=${PYTHON_BIN} not found" >&2
    return 1
  fi

  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  echo "ERROR: Python interpreter not found (tried python3.13/python3.12/python3.11/python3/python)" >&2
  return 1
}

PY_BIN="$(resolve_python_bin)"

if [[ ! -f "${ROOT_DIR}/.env.production" ]]; then
  cp "${ROOT_DIR}/.env.production.example" "${ROOT_DIR}/.env.production"
  PREPARED_PROD_ENV=1
fi

run_step "Bash syntax for release smoke scripts" \
  bash -n \
  ./scripts/check_migration_filenames.sh \
  ./scripts/lib/release_smoke_payloads.sh \
  ./scripts/post_deploy_production_gate.sh \
  ./scripts/staging_smoke_e2e_gate.sh \
  ./scripts/e2e_test.sh \
  ./scripts/validate-env-production.sh

run_step "Migration filename validation" \
  ./scripts/check_migration_filenames.sh

run_step "Production docker compose validation" \
  docker compose --env-file .env.production.example -f docker-compose.prod.yml config -q

run_step "Production config contract consistency" \
  "${PY_BIN}" ./scripts/production_config_contract.py check-consistency

run_step "Backend release smoke contract tests" \
  bash -lc "GO_TOOLCHAIN='${GO_TOOLCHAIN}' GO_GOMODCACHE='${GO_GOMODCACHE}' GO_GOCACHE='${GO_GOCACHE}' ./scripts/go_test_backend.sh ./internal/api -run '^TestReleaseSmoke' -count=1"

echo ""
echo "[release-preflight] All checks passed."
