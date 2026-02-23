#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI_DIR="$ROOT_DIR/ai-service"

cd "$AI_DIR"

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

choose_python_ge_311() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      echo "PYTHON_BIN=$PYTHON_BIN not found" >&2
      return 1
    fi
    if ! is_python_ge_311 "$PYTHON_BIN"; then
      echo "PYTHON_BIN=$PYTHON_BIN has version $(python_version "$PYTHON_BIN"), need >=3.11" >&2
      return 1
    fi
    echo "$PYTHON_BIN"
    return 0
  fi

  if [[ -x ".venv/bin/python" ]] && is_python_ge_311 ".venv/bin/python"; then
    echo ".venv/bin/python"
    return 0
  fi

  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && is_python_ge_311 "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

choose_python_with_pytest() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
      echo "PYTHON_BIN=$PYTHON_BIN not found" >&2
      return 1
    fi
    if ! is_python_ge_311 "$PYTHON_BIN"; then
      echo "PYTHON_BIN=$PYTHON_BIN has version $(python_version "$PYTHON_BIN"), need >=3.11" >&2
      return 1
    fi
    if has_pytest "$PYTHON_BIN"; then
      echo "$PYTHON_BIN"
      return 0
    fi
    echo "pytest is not available for PYTHON_BIN=$PYTHON_BIN" >&2
    return 1
  fi

  if [[ -x ".venv/bin/python" ]] && is_python_ge_311 ".venv/bin/python" && has_pytest ".venv/bin/python"; then
    echo ".venv/bin/python"
    return 0
  fi

  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && is_python_ge_311 "$candidate" && has_pytest "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

if PY_BIN="$(choose_python_with_pytest)"; then
  exec "$PY_BIN" -m pytest "$@"
fi

if ! PY_BIN="$(choose_python_ge_311)"; then
  echo "pytest requires Python >=3.11 (PYTHON_BIN/.venv/python/python3.{11,12,13})" >&2
  echo "Use one of the following:" >&2
  echo "  1) PYTHON_BIN=python3.11 ./scripts/pytest_ai.sh" >&2
  echo "  2) cd ai-service && python3.11 -m pip install -r requirements.txt" >&2
  exit 1
fi

if [[ "${PYTEST_AI_AUTO_INSTALL:-0}" == "1" ]]; then
  echo "pytest is missing for ${PY_BIN}; installing ai-service requirements (PYTEST_AI_AUTO_INSTALL=1)..."
  "$PY_BIN" -m pip install -r requirements.txt
else
  echo "Found Python $(python_version "$PY_BIN") at '${PY_BIN}', but pytest is not installed for this interpreter." >&2
  echo "Install dependencies and retry:" >&2
  echo "  ${PY_BIN} -m pip install -r requirements.txt" >&2
  echo "Or run once with auto-install:" >&2
  echo "  PYTEST_AI_AUTO_INSTALL=1 ./scripts/pytest_ai.sh" >&2
  exit 1
fi

exec "$PY_BIN" -m pytest "$@"
