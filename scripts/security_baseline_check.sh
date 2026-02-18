#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PY_USER_BASE="${ROOT_DIR}/.pyuserbase"

PYTHON_BIN="${PYTHON_BIN:-}"
GO_TOOLCHAIN="${GO_TOOLCHAIN:-go1.25.7}"
PY_MODE="local"
PIP_AUDIT_REQ_PATH="ai-service/requirements.txt"

echo "[security] Validating pinned baseline versions"

if ! grep -qE '^toolchain go1\.25\.7$' backend/go.mod; then
  echo "ERROR: backend/go.mod must contain 'toolchain go1.25.7'"
  exit 1
fi

is_python_ge_311() {
  "$@" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

python_version() {
  "$@" - <<'PY' 2>/dev/null
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
}

PY_CMD=()

if [[ -n "$PYTHON_BIN" ]]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: PYTHON_BIN=$PYTHON_BIN not found."
    exit 1
  fi
  if ! is_python_ge_311 "$PYTHON_BIN"; then
    echo "ERROR: PYTHON_BIN=$PYTHON_BIN has version $(python_version "$PYTHON_BIN"), need >=3.11"
    exit 1
  fi
  PY_CMD=("$PYTHON_BIN")
else
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && is_python_ge_311 "$candidate"; then
      PY_CMD=("$candidate")
      break
    fi
  done
  if [[ ${#PY_CMD[@]} -eq 0 ]]; then
    if command -v docker >/dev/null 2>&1 && docker image inspect siteparserforfreelans-ai-service >/dev/null 2>&1 && \
      is_python_ge_311 docker run --rm -i --entrypoint python siteparserforfreelans-ai-service; then
      PY_MODE="docker"
      PY_CMD=(docker run --rm -i --entrypoint python siteparserforfreelans-ai-service)
      PIP_AUDIT_REQ_PATH="/app/requirements.txt"
    fi
  fi
  if [[ ${#PY_CMD[@]} -eq 0 ]]; then
    echo "ERROR: Python >=3.11 not found (and docker ai-service image fallback is unavailable)."
    echo "Install Python >=3.11 or build image: docker compose build ai-service."
    exit 1
  fi
fi

echo "[security] Using Python interpreter ($(printf '%s ' "${PY_CMD[@]}")) version $(python_version "${PY_CMD[@]}") [mode=${PY_MODE}]"

if ! grep -qE '^requires-python = ">=3\.11"$' ai-service/pyproject.toml; then
  echo "ERROR: ai-service/pyproject.toml must contain: requires-python = \">=3.11\""
  exit 1
fi

if ! grep -qiE '"pillow>=12\.1\.1"' ai-service/pyproject.toml; then
  echo "ERROR: ai-service/pyproject.toml must include pillow>=12.1.1 in [project].dependencies"
  exit 1
fi

if ! grep -qiE '^pillow==12\.1\.1$' ai-service/requirements.txt; then
  echo "ERROR: ai-service/requirements.txt must pin pillow==12.1.1"
  exit 1
fi

echo "[security] Running govulncheck with ${GO_TOOLCHAIN}"
(cd backend && GOTOOLCHAIN="${GO_TOOLCHAIN}" go run golang.org/x/vuln/cmd/govulncheck@latest ./...)

echo "[security] Running pip-audit"
if [[ "$PY_MODE" == "docker" ]]; then
  docker run --rm --entrypoint sh siteparserforfreelans-ai-service -lc \
    'python -m pip install --upgrade pip pip-audit >/dev/null && python -m pip_audit -r /app/requirements.txt'
else
  PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUSERBASE="$PY_USER_BASE" HOME="$ROOT_DIR" \
    "${PY_CMD[@]}" -m pip install --upgrade --user pip pip-audit >/dev/null
  PYTHONUSERBASE="$PY_USER_BASE" HOME="$ROOT_DIR" \
    "${PY_CMD[@]}" -m pip_audit -r "$PIP_AUDIT_REQ_PATH"
fi

echo "[security] Baseline checks passed"
