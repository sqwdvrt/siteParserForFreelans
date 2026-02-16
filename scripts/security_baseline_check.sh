#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
GO_TOOLCHAIN="${GO_TOOLCHAIN:-go1.25.7}"

echo "[security] Validating pinned baseline versions"

if ! grep -qE '^toolchain go1\.25\.7$' backend/go.mod; then
  echo "ERROR: backend/go.mod must contain 'toolchain go1.25.7'"
  exit 1
fi

is_python_ge_311() {
  local py_bin="$1"
  "$py_bin" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

python_version() {
  local py_bin="$1"
  "$py_bin" - <<'PY' 2>/dev/null
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
}

if [[ -n "$PYTHON_BIN" ]]; then
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "ERROR: PYTHON_BIN=$PYTHON_BIN not found."
    exit 1
  fi
  if ! is_python_ge_311 "$PYTHON_BIN"; then
    echo "ERROR: PYTHON_BIN=$PYTHON_BIN has version $(python_version "$PYTHON_BIN"), need >=3.11"
    exit 1
  fi
else
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && is_python_ge_311 "$candidate"; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
  if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: Python >=3.11 not found. Install Python >=3.11 or set PYTHON_BIN."
    exit 1
  fi
fi

echo "[security] Using Python interpreter: ${PYTHON_BIN} (version $(python_version "$PYTHON_BIN"))"

"$PYTHON_BIN" - <<'PY'
import pathlib
import sys
import tomllib

pyproject_path = pathlib.Path("ai-service/pyproject.toml")
data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
project = data.get("project", {})

requires_python = str(project.get("requires-python", "")).strip()
if requires_python != ">=3.11":
    print(f"ERROR: requires-python must be '>=3.11', got: {requires_python!r}")
    sys.exit(1)

deps = [str(d) for d in project.get("dependencies", [])]
if not any(dep.lower().startswith("pillow>=12.1.1") for dep in deps):
    print("ERROR: pyproject dependencies must include pillow>=12.1.1")
    sys.exit(1)
PY

if ! grep -qiE '^pillow>=12\.1\.1$' ai-service/requirements.txt; then
  echo "ERROR: ai-service/requirements.txt must pin pillow>=12.1.1"
  exit 1
fi

echo "[security] Running govulncheck with ${GO_TOOLCHAIN}"
(cd backend && GOTOOLCHAIN="${GO_TOOLCHAIN}" go run golang.org/x/vuln/cmd/govulncheck@latest ./...)

echo "[security] Running pip-audit"
"$PYTHON_BIN" -m pip install --upgrade pip pip-audit >/dev/null
"$PYTHON_BIN" -m pip_audit -r ai-service/requirements.txt

echo "[security] Baseline checks passed"
