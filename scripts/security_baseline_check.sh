#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PY_USER_BASE="${ROOT_DIR}/.pyuserbase"

PYTHON_BIN="${PYTHON_BIN:-}"
GO_TOOLCHAIN="${GO_TOOLCHAIN:-go1.25.9}"
GO_GOMODCACHE="${GO_GOMODCACHE:-${ROOT_DIR}/backend/.gomodcache-${GO_TOOLCHAIN}}"
GO_GOCACHE="${GO_GOCACHE:-${ROOT_DIR}/backend/.gocache-${GO_TOOLCHAIN}}"
PY_MODE="local"
PIP_AUDIT_REQ_PATH="ai-service/requirements.txt"
AI_VENV_PYTHON="${ROOT_DIR}/ai-service/.venv/bin/python"
SECURITY_BASELINE_SKIP_PIP_AUDIT="${SECURITY_BASELINE_SKIP_PIP_AUDIT:-0}"
PIP_AUDIT_ARGS=(--no-deps -r "$PIP_AUDIT_REQ_PATH" --ignore-vuln CVE-2026-4539)

echo "[security] Validating pinned baseline versions"

if ! grep -qE '^toolchain go1\.25\.9$' backend/go.mod; then
  echo "ERROR: backend/go.mod must contain 'toolchain go1.25.9'"
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

is_python_virtualenv() {
  "$@" - <<'PY' >/dev/null 2>&1
import sys
in_venv = (
    getattr(sys, "base_prefix", sys.prefix) != sys.prefix
    or hasattr(sys, "real_prefix")
)
raise SystemExit(0 if in_venv else 1)
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
  if [[ -x "${AI_VENV_PYTHON}" ]] && is_python_ge_311 "${AI_VENV_PYTHON}"; then
    PY_CMD=("${AI_VENV_PYTHON}")
  fi

  if [[ ${#PY_CMD[@]} -eq 0 ]]; then
    for candidate in python3.13 python3.12 python3.11 python3; do
      if command -v "$candidate" >/dev/null 2>&1 && is_python_ge_311 "$candidate"; then
        PY_CMD=("$candidate")
        break
      fi
    done
  fi

  if [[ ${#PY_CMD[@]} -eq 0 ]]; then
    if command -v python >/dev/null 2>&1 && is_python_ge_311 python; then
      PY_CMD=("python")
    fi
  fi

  if [[ ${#PY_CMD[@]} -eq 0 ]]; then
    if command -v docker >/dev/null 2>&1 && docker image inspect siteparserforfreelans-ai-service >/dev/null 2>&1 && \
      is_python_ge_311 docker run --rm -i --entrypoint python siteparserforfreelans-ai-service; then
      PY_MODE="docker"
      PY_CMD=(docker run --rm -i --entrypoint python siteparserforfreelans-ai-service)
      PIP_AUDIT_REQ_PATH="/app/requirements.txt"
    fi
  fi
  if [[ ${#PY_CMD[@]} -eq 0 ]]; then
    echo "ERROR: Python >=3.11 not found (tried PYTHON_BIN, ai-service/.venv, python3.*, python3, python; docker fallback unavailable)."
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

REQ_SPEC_REGEX='^[A-Za-z0-9_.-]+(\[[A-Za-z0-9_,.-]+\])?==[^[:space:]#;]+([[:space:]]*;[[:space:]]*.+)?$'
BAD_REQ_LINES=()
REQ_LINE_NO=0
while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
  REQ_LINE_NO=$((REQ_LINE_NO + 1))
  req_line="${raw_line%%#*}"
  req_line="$(printf '%s' "$req_line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  [[ -z "$req_line" ]] && continue
  if [[ ! "$req_line" =~ $REQ_SPEC_REGEX ]]; then
    BAD_REQ_LINES+=("${REQ_LINE_NO}:${raw_line}")
  fi
done < ai-service/requirements.txt
if (( ${#BAD_REQ_LINES[@]} > 0 )); then
  echo "ERROR: ai-service/requirements.txt must contain only pinned specs ('package==version')."
  echo "Invalid lines:"
  printf '  %s\n' "${BAD_REQ_LINES[@]}"
  exit 1
fi

echo "[security] Running govulncheck with ${GO_TOOLCHAIN}"
mkdir -p "${GO_GOMODCACHE}" "${GO_GOCACHE}"
(
  cd backend && \
    GOTOOLCHAIN="${GO_TOOLCHAIN}" \
    GOMODCACHE="${GO_GOMODCACHE}" \
    GOCACHE="${GO_GOCACHE}" \
    go run golang.org/x/vuln/cmd/govulncheck@v1.1.4 ./...
)

if [[ "$SECURITY_BASELINE_SKIP_PIP_AUDIT" == "1" ]]; then
  echo "[security] Skipping pip-audit (SECURITY_BASELINE_SKIP_PIP_AUDIT=1)"
else
  echo "[security] Running pip-audit"
  if [[ "$PY_MODE" == "docker" ]]; then
    docker run --rm --entrypoint sh siteparserforfreelans-ai-service -lc \
      'python -m pip install --upgrade pip pip-audit >/dev/null && python -m pip_audit --no-deps -r /app/requirements.txt --ignore-vuln CVE-2026-4539'
  else
    if is_python_virtualenv "${PY_CMD[@]}"; then
      echo "[security] Detected virtualenv Python; installing pip-audit into venv (without --user)"
      PIP_DISABLE_PIP_VERSION_CHECK=1 \
        "${PY_CMD[@]}" -m pip install --upgrade pip pip-audit >/dev/null
      "${PY_CMD[@]}" -m pip_audit "${PIP_AUDIT_ARGS[@]}"
    else
      PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUSERBASE="$PY_USER_BASE" HOME="$ROOT_DIR" \
        "${PY_CMD[@]}" -m pip install --upgrade --user pip pip-audit >/dev/null
      PYTHONUSERBASE="$PY_USER_BASE" HOME="$ROOT_DIR" \
        "${PY_CMD[@]}" -m pip_audit "${PIP_AUDIT_ARGS[@]}"
    fi
  fi
fi

echo "[security] Baseline checks passed"
