#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "ERROR: python3/python is required to validate the production env contract" >&2
  exit 1
fi

exec "$PYTHON_BIN" "${SCRIPT_DIR}/production_config_contract.py" validate-env "${1:-.env.production}"
