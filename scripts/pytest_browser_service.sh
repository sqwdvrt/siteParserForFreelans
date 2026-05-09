#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BROWSER_DIR="${ROOT_DIR}/browser-service"

cd "${BROWSER_DIR}"

is_python_ge_311() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

python_version() {
  "$1" - <<'PY' 2>/dev/null
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
}

has_pytest() {
  "$1" -m pytest --version >/dev/null 2>&1
}

resolve_python_with_pytest() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
      echo "ERROR: PYTHON_BIN=${PYTHON_BIN} not found" >&2
      return 1
    fi
    if ! is_python_ge_311 "${PYTHON_BIN}"; then
      echo "ERROR: PYTHON_BIN=${PYTHON_BIN} has version $(python_version "${PYTHON_BIN}"), need >=3.11" >&2
      return 1
    fi
    if has_pytest "${PYTHON_BIN}"; then
      printf '%s\n' "${PYTHON_BIN}"
      return 0
    fi
    echo "ERROR: pytest is not available for PYTHON_BIN=${PYTHON_BIN}" >&2
    return 1
  fi

  if [[ -x ".venv/bin/python" ]] && is_python_ge_311 ".venv/bin/python" && has_pytest ".venv/bin/python"; then
    printf '%s\n' ".venv/bin/python"
    return 0
  fi

  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && is_python_ge_311 "${candidate}" && has_pytest "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

if PY_BIN="$(resolve_python_with_pytest)"; then
  exec "${PY_BIN}" -m pytest "$@"
fi

if [[ "${PYTEST_BROWSER_SERVICE_AUTO_INSTALL:-0}" == "1" ]]; then
  "${ROOT_DIR}/scripts/bootstrap_browser_service_venv.sh"
  exec "${BROWSER_DIR}/.venv/bin/python" -m pytest "$@"
fi

echo "ERROR: pytest is unavailable for browser-service." >&2
echo "Bootstrap a local venv once:" >&2
echo "  ./scripts/bootstrap_browser_service_venv.sh" >&2
echo "Or run with an explicit interpreter that already has pytest:" >&2
echo "  PYTHON_BIN=python3.11 ./scripts/pytest_browser_service.sh -q" >&2
exit 1
