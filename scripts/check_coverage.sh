#!/usr/bin/env bash
# Coverage gate:
# - backend/internal >= BACKEND_INTERNAL_COVERAGE_MIN (default 70)
# - ai-service/src >= AI_SERVICE_COVERAGE_MIN (default 70)
# - telegram-bot TOTAL >= TELEGRAM_BOT_COVERAGE_MIN (default 80)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_INTERNAL_COVERAGE_MIN="${BACKEND_INTERNAL_COVERAGE_MIN:-70}"
AI_SERVICE_COVERAGE_MIN="${AI_SERVICE_COVERAGE_MIN:-70}"
TELEGRAM_BOT_COVERAGE_MIN="${TELEGRAM_BOT_COVERAGE_MIN:-80}"
GO_COVERAGE_TOOLCHAIN="${GO_COVERAGE_TOOLCHAIN:-go1.25.9}"
BACKEND_GOMODCACHE="${BACKEND_GOMODCACHE:-${ROOT_DIR}/backend/.gomodcache-${GO_COVERAGE_TOOLCHAIN}}"
BACKEND_GOCACHE="${BACKEND_GOCACHE:-${ROOT_DIR}/backend/.gocache-${GO_COVERAGE_TOOLCHAIN}}"

cleanup() {
  [[ -n "${GO_COVER_FILE:-}" && -f "${GO_COVER_FILE}" ]] && rm -f "${GO_COVER_FILE}"
  [[ -n "${AI_COVER_LOG:-}" && -f "${AI_COVER_LOG}" ]] && rm -f "${AI_COVER_LOG}"
  [[ -n "${TG_COVER_LOG:-}" && -f "${TG_COVER_LOG}" ]] && rm -f "${TG_COVER_LOG}"
  true
}
trap cleanup EXIT

is_coverage_ok() {
  local actual="$1"
  local min="$2"
  awk -v actual="$actual" -v min="$min" 'BEGIN { exit (actual + 0 >= min + 0) ? 0 : 1 }'
}

choose_python_cmd() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
      if has_pytest_cov "${PYTHON_BIN}"; then
        echo "${PYTHON_BIN}"
        return 0
      fi
      echo "WARN: PYTHON_BIN=${PYTHON_BIN} does not have pytest-cov; trying fallback interpreters" >&2
    else
      echo "WARN: PYTHON_BIN=${PYTHON_BIN} not found; trying fallback interpreters" >&2
    fi
  fi

  if [[ -x ".venv/bin/python" ]] && has_pytest_cov ".venv/bin/python"; then
    echo ".venv/bin/python"
    return 0
  fi

  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && has_pytest_cov "${candidate}"; then
      echo "${candidate}"
      return 0
    fi
  done

  echo "ERROR: Python interpreter with pytest-cov not found (tried PYTHON_BIN, .venv/bin/python, python3.13, python3.12, python3.11, python3, python)" >&2
  return 1
}

has_pytest_cov() {
  "$1" -c 'import importlib.util, sys; raise SystemExit(0 if importlib.util.find_spec("pytest_cov") else 1)' >/dev/null 2>&1 \
    || "$1" -m pytest --help 2>/dev/null | grep -q -- '--cov'
}

ensure_pytest_coverage_tools() {
  local pybin="$1"
  if has_pytest_cov "${pybin}"; then
    return 0
  fi

  echo "ERROR: pytest-cov is unavailable for ${pybin}. Install dependencies before running coverage gate:" >&2
  echo "  ${pybin} -m pip install \"pytest>=7.0\" \"pytest-cov>=4.0\"" >&2
  return 1
}

ensure_go_covdata() {
  local probe
  set +e
  probe="$(go tool covdata 2>&1)"
  set -e
  if [[ "${probe}" != *'no such tool "covdata"'* ]]; then
    return 0
  fi

  echo "WARN: current Go toolchain has no covdata; retrying with GOTOOLCHAIN=${GO_COVERAGE_TOOLCHAIN}"
  export GOTOOLCHAIN="${GO_COVERAGE_TOOLCHAIN}"

  set +e
  probe="$(go tool covdata 2>&1)"
  set -e
  if [[ "${probe}" == *'no such tool "covdata"'* ]]; then
    echo "ERROR: go tool covdata is still unavailable after switching toolchain."
    echo "Current go version: $(go version || true)"
    echo "Set GO_COVERAGE_TOOLCHAIN to a working toolchain (example: go1.25.9)."
    exit 1
  fi
}

echo "=== Coverage Gate ==="
echo "Minimums: backend/internal >= ${BACKEND_INTERNAL_COVERAGE_MIN}%, ai-service >= ${AI_SERVICE_COVERAGE_MIN}%, telegram-bot >= ${TELEGRAM_BOT_COVERAGE_MIN}%"

echo ""
echo "=== Go coverage: backend/internal ==="
export GOTOOLCHAIN="${GO_COVERAGE_TOOLCHAIN}"
mkdir -p "${BACKEND_GOMODCACHE}" "${BACKEND_GOCACHE}"
ensure_go_covdata
cd "${ROOT_DIR}/backend"
GO_COVER_FILE="$(mktemp)"

# backend/internal/adapter/postgres currently has integration-only tests (build tags),
# so it is excluded from the unit coverage gate to avoid skewing local/CI unit metrics.
BACKEND_UNIT_PACKAGES=()
while IFS= read -r pkg; do
  BACKEND_UNIT_PACKAGES+=("${pkg}")
done < <(
  GOMODCACHE="${BACKEND_GOMODCACHE}" GOCACHE="${BACKEND_GOCACHE}" \
    go list ./internal/... | grep -v '/internal/adapter/postgres$'
)
if [[ ${#BACKEND_UNIT_PACKAGES[@]} -eq 0 ]]; then
  echo "ERROR: failed to resolve backend unit packages for coverage"
  exit 1
fi
GOMODCACHE="${BACKEND_GOMODCACHE}" GOCACHE="${BACKEND_GOCACHE}" \
  go test "${BACKEND_UNIT_PACKAGES[@]}" -covermode=atomic -coverprofile="${GO_COVER_FILE}" -count=1

GO_TOTAL_RAW="$(GOMODCACHE="${BACKEND_GOMODCACHE}" GOCACHE="${BACKEND_GOCACHE}" go tool cover -func="${GO_COVER_FILE}" | awk '/^total:/ {print $3}')"
if [[ -z "${GO_TOTAL_RAW}" ]]; then
  echo "ERROR: failed to parse backend/internal coverage"
  exit 1
fi
GO_TOTAL="${GO_TOTAL_RAW%\%}"
echo "backend/internal coverage: ${GO_TOTAL}%"
if ! is_coverage_ok "${GO_TOTAL}" "${BACKEND_INTERNAL_COVERAGE_MIN}"; then
  echo "ERROR: backend/internal coverage gate failed (${GO_TOTAL}% < ${BACKEND_INTERNAL_COVERAGE_MIN}%)"
  exit 1
fi

echo ""
echo "=== Python coverage: ai-service/src ==="
cd "${ROOT_DIR}"
AI_COVER_LOG="$(mktemp)"
set +e
PYTEST_AI_AUTO_INSTALL=0 ./scripts/pytest_ai.sh --cov=src --cov-report=term-missing -q | tee "${AI_COVER_LOG}"
PYTEST_STATUS=${PIPESTATUS[0]}
set -e
if [[ ${PYTEST_STATUS} -ne 0 ]]; then
  echo "ERROR: ai-service tests failed before coverage gate check"
  exit ${PYTEST_STATUS}
fi

AI_TOTAL_RAW="$(awk '/^TOTAL/ {for (i=1; i<=NF; i++) if ($i ~ /%$/) pct=$i} END {print pct}' "${AI_COVER_LOG}")"
if [[ -z "${AI_TOTAL_RAW}" ]]; then
  echo "ERROR: failed to parse ai-service coverage"
  exit 1
fi
AI_TOTAL="${AI_TOTAL_RAW%\%}"
echo "ai-service coverage: ${AI_TOTAL}%"
if ! is_coverage_ok "${AI_TOTAL}" "${AI_SERVICE_COVERAGE_MIN}"; then
  echo "ERROR: ai-service coverage gate failed (${AI_TOTAL}% < ${AI_SERVICE_COVERAGE_MIN}%)"
  exit 1
fi

echo ""
echo "=== Python coverage: telegram-bot TOTAL ==="
cd "${ROOT_DIR}/telegram-bot"
TG_COVER_LOG="$(mktemp)"
TG_PYTHON_BIN="$(choose_python_cmd)"
ensure_pytest_coverage_tools "${TG_PYTHON_BIN}"
set +e
"${TG_PYTHON_BIN}" -m pytest --cov=. --cov-report=term-missing -q | tee "${TG_COVER_LOG}"
PYTEST_TG_STATUS=${PIPESTATUS[0]}
set -e
if [[ ${PYTEST_TG_STATUS} -ne 0 ]]; then
  echo "ERROR: telegram-bot tests failed before coverage gate check"
  exit ${PYTEST_TG_STATUS}
fi

TG_TOTAL_RAW="$(awk '/^TOTAL/ {for (i=1; i<=NF; i++) if ($i ~ /%$/) pct=$i} END {print pct}' "${TG_COVER_LOG}")"
if [[ -z "${TG_TOTAL_RAW}" ]]; then
  echo "ERROR: failed to parse telegram-bot total coverage"
  exit 1
fi
TG_TOTAL="${TG_TOTAL_RAW%\%}"
echo "telegram-bot total coverage: ${TG_TOTAL}%"
if ! is_coverage_ok "${TG_TOTAL}" "${TELEGRAM_BOT_COVERAGE_MIN}"; then
  echo "ERROR: telegram-bot total coverage gate failed (${TG_TOTAL}% < ${TELEGRAM_BOT_COVERAGE_MIN}%)"
  exit 1
fi

echo ""
echo "Coverage gate PASSED"
