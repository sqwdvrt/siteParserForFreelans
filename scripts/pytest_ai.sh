#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI_DIR="$ROOT_DIR/ai-service"

cd "$AI_DIR"

choose_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
      echo "$PYTHON_BIN"
      return 0
    fi
    return 1
  fi

  if [[ -x ".venv/bin/python" ]] && .venv/bin/python -m pytest --version >/dev/null 2>&1; then
    echo ".venv/bin/python"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1 && python3 -m pytest --version >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi

  return 1
}

if ! PY_BIN="$(choose_python)"; then
  echo "pytest is not available for PYTHON_BIN/.venv/python/python3" >&2
  echo "Use one of the following:" >&2
  echo "  1) PYTHON_BIN=python3.11 ./scripts/pytest_ai.sh" >&2
  echo "  2) cd ai-service && python3 -m pip install -r requirements.txt" >&2
  exit 1
fi

exec "$PY_BIN" -m pytest "$@"
