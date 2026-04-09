#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GO_TOOLCHAIN="${GO_TOOLCHAIN:-go1.25.9}"
GO_GOMODCACHE="${GO_GOMODCACHE:-${ROOT_DIR}/backend/.gomodcache-${GO_TOOLCHAIN}}"
GO_GOCACHE="${GO_GOCACHE:-${ROOT_DIR}/backend/.gocache-${GO_TOOLCHAIN}}"
PYTHON_BIN="${PYTHON_BIN:-}"

run_step() {
  local name="$1"
  shift
  echo ""
  echo "=== [full-check] ${name} ==="
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

run_actionlint() {
  if command -v actionlint >/dev/null 2>&1; then
    actionlint
    return 0
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: actionlint not found and docker is unavailable." >&2
    echo "Install actionlint or docker, or set SKIP_ACTIONLINT=1." >&2
    return 1
  fi

  docker run --rm -v "${ROOT_DIR}:/repo" -w /repo rhysd/actionlint:1.7.8
}

PY_BIN="$(resolve_python_bin)"

if [[ "${SKIP_ACTIONLINT:-0}" != "1" ]]; then
  run_step "Workflow lint (actionlint)" run_actionlint
else
  echo "[full-check] SKIP_ACTIONLINT=1, skipping workflow lint"
fi

run_step "Backend unit tests" bash -lc "GO_TOOLCHAIN='${GO_TOOLCHAIN}' GO_GOMODCACHE='${GO_GOMODCACHE}' GO_GOCACHE='${GO_GOCACHE}' bash ./scripts/go_test_backend.sh ./... -count=1"
run_step "AI unit tests" ./scripts/pytest_ai.sh -q
run_step "Telegram bot unit tests" ./scripts/pytest_telegram_bot.sh -q
run_step "Coverage gate" bash -lc "PYTHON_BIN='${PY_BIN}' ./scripts/check_coverage.sh"
run_step "Monitoring config gate" ./scripts/monitoring_config_check.sh
run_step "Security baseline gate" bash -lc "PYTHON_BIN='${PY_BIN}' GO_TOOLCHAIN='${GO_TOOLCHAIN}' GO_GOMODCACHE='${GO_GOMODCACHE}' GO_GOCACHE='${GO_GOCACHE}' ./scripts/security_baseline_check.sh"

echo ""
echo "[full-check] All checks passed."
