#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_DIR="${ROOT_DIR}/telegram-bot"
VENV_DIR="${BOT_DIR}/.venv"

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

resolve_python_bin() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
      echo "ERROR: PYTHON_BIN=${PYTHON_BIN} not found" >&2
      return 1
    fi
    if ! is_python_ge_311 "${PYTHON_BIN}"; then
      echo "ERROR: PYTHON_BIN=${PYTHON_BIN} has version $(python_version "${PYTHON_BIN}"), need >=3.11" >&2
      return 1
    fi
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi

  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && is_python_ge_311 "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  echo "ERROR: Python >=3.11 not found (tried PYTHON_BIN, python3.13, python3.12, python3.11, python3, python)." >&2
  return 1
}

PY_BIN="$(resolve_python_bin)"

echo "Using Python $(python_version "${PY_BIN}") from '${PY_BIN}'"
"${PY_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -e "${BOT_DIR}[dev]"

echo ""
echo "telegram-bot venv is ready: ${VENV_DIR}"
echo "Run tests with:"
echo "  ./scripts/pytest_telegram_bot.sh -q"
echo "Or directly:"
echo "  ${VENV_DIR}/bin/python -m pytest -q"
