#!/usr/bin/env bash
# Coverage gate:
# - backend/internal >= BACKEND_INTERNAL_COVERAGE_MIN (default 70)
# - ai-service/src >= AI_SERVICE_COVERAGE_MIN (default 70)
# - telegram-bot/main.py >= TELEGRAM_BOT_COVERAGE_MIN (default 80)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_INTERNAL_COVERAGE_MIN="${BACKEND_INTERNAL_COVERAGE_MIN:-70}"
AI_SERVICE_COVERAGE_MIN="${AI_SERVICE_COVERAGE_MIN:-70}"
TELEGRAM_BOT_COVERAGE_MIN="${TELEGRAM_BOT_COVERAGE_MIN:-80}"
GO_COVERAGE_TOOLCHAIN="${GO_COVERAGE_TOOLCHAIN:-go1.25.7}"
BACKEND_GOMODCACHE="${BACKEND_GOMODCACHE:-${ROOT_DIR}/backend/.gomodcache-${GO_COVERAGE_TOOLCHAIN}}"
BACKEND_GOCACHE="${BACKEND_GOCACHE:-${ROOT_DIR}/backend/.gocache-${GO_COVERAGE_TOOLCHAIN}}"
PYTEST_AI_AUTO_INSTALL="${PYTEST_AI_AUTO_INSTALL:-1}"
PYTEST_TG_AUTO_INSTALL="${PYTEST_TG_AUTO_INSTALL:-1}"

cleanup() {
  [[ -n "${GO_COVER_FILE:-}" && -f "${GO_COVER_FILE}" ]] && rm -f "${GO_COVER_FILE}"
  [[ -n "${AI_COVER_LOG:-}" && -f "${AI_COVER_LOG}" ]] && rm -f "${AI_COVER_LOG}"
  [[ -n "${TG_COVER_LOG:-}" && -f "${TG_COVER_LOG}" ]] && rm -f "${TG_COVER_LOG}"
  [[ -n "${TG_COV_VENV_DIR:-}" && -d "${TG_COV_VENV_DIR}" ]] && rm -rf "${TG_COV_VENV_DIR}"
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
      echo "${PYTHON_BIN}"
      return 0
    fi
    echo "ERROR: PYTHON_BIN=${PYTHON_BIN} not found" >&2
    return 1
  fi

  if [[ -x ".venv/bin/python" ]]; then
    echo ".venv/bin/python"
    return 0
  fi

  for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done

  echo "ERROR: Python interpreter not found (tried PYTHON_BIN, .venv/bin/python, python3, python)" >&2
  return 1
}

is_python_virtualenv() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
in_venv = (
    getattr(sys, "base_prefix", sys.prefix) != sys.prefix
    or hasattr(sys, "real_prefix")
)
raise SystemExit(0 if in_venv else 1)
PY
}

has_pytest_cov() {
  "$1" -m pytest --help 2>/dev/null | grep -q -- '--cov'
}

ensure_tg_pytest_coverage_tools() {
  local pybin="$1"
  if has_pytest_cov "${pybin}"; then
    echo "${pybin}"
    return 0
  fi

  if [[ "${PYTEST_TG_AUTO_INSTALL}" != "1" ]]; then
    echo "ERROR: pytest/pytest-cov is unavailable for ${pybin}; set PYTEST_TG_AUTO_INSTALL=1 to auto-install" >&2
    return 1
  fi

  echo "WARN: pytest/pytest-cov not found for ${pybin}; installing..." >&2
  if is_python_virtualenv "${pybin}"; then
    PIP_DISABLE_PIP_VERSION_CHECK=1 "${pybin}" -m pip install --upgrade pytest pytest-cov >/dev/null
    echo "${pybin}"
    return 0
  else
    # Prefer global install first (works on many CI runners).
    if PIP_DISABLE_PIP_VERSION_CHECK=1 "${pybin}" -m pip install --upgrade pytest pytest-cov >/dev/null 2>&1; then
      echo "${pybin}"
      return 0
    fi

    # Fallback: isolate tools into a temporary venv to avoid externally-managed Python/user-site issues.
    TG_COV_VENV_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tg-cov-venv.XXXXXX")"
    "${pybin}" -m venv "${TG_COV_VENV_DIR}"
    PIP_DISABLE_PIP_VERSION_CHECK=1 "${TG_COV_VENV_DIR}/bin/python" -m pip install --upgrade pip pytest pytest-cov >/dev/null
    echo "${TG_COV_VENV_DIR}/bin/python"
    return 0
  fi

  echo "ERROR: failed to install pytest/pytest-cov for ${pybin}" >&2
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
    echo "Set GO_COVERAGE_TOOLCHAIN to a working toolchain (example: go1.25.7)."
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
PYTEST_AI_AUTO_INSTALL="${PYTEST_AI_AUTO_INSTALL}" ./scripts/pytest_ai.sh --cov=src --cov-report=term-missing -q | tee "${AI_COVER_LOG}"
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
echo "=== Python coverage: telegram-bot/main.py ==="
cd "${ROOT_DIR}/telegram-bot"
TG_COVER_LOG="$(mktemp)"
TG_PYTHON_BIN="$(choose_python_cmd)"
TG_PYTHON_BIN="$(ensure_tg_pytest_coverage_tools "${TG_PYTHON_BIN}")"
set +e
"${TG_PYTHON_BIN}" -m pytest --cov=. --cov-report=term-missing -q | tee "${TG_COVER_LOG}"
PYTEST_TG_STATUS=${PIPESTATUS[0]}
set -e
if [[ ${PYTEST_TG_STATUS} -ne 0 ]]; then
  echo "ERROR: telegram-bot tests failed before coverage gate check"
  exit ${PYTEST_TG_STATUS}
fi

TG_TOTAL_RAW="$(awk '$1 ~ /(^|\/)main\.py$/ {for (i=1; i<=NF; i++) if ($i ~ /%$/) pct=$i} END {print pct}' "${TG_COVER_LOG}")"
if [[ -z "${TG_TOTAL_RAW}" ]]; then
  echo "ERROR: failed to parse telegram-bot main.py coverage"
  exit 1
fi
TG_TOTAL="${TG_TOTAL_RAW%\%}"
echo "telegram-bot coverage: ${TG_TOTAL}%"
if ! is_coverage_ok "${TG_TOTAL}" "${TELEGRAM_BOT_COVERAGE_MIN}"; then
  echo "ERROR: telegram-bot coverage gate failed (${TG_TOTAL}% < ${TELEGRAM_BOT_COVERAGE_MIN}%)"
  exit 1
fi

echo ""
echo "Coverage gate PASSED"
