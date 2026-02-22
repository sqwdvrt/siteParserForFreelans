#!/usr/bin/env bash
# Coverage gate:
# - backend/internal >= BACKEND_INTERNAL_COVERAGE_MIN (default 70)
# - ai-service/src >= AI_SERVICE_COVERAGE_MIN (default 70)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_INTERNAL_COVERAGE_MIN="${BACKEND_INTERNAL_COVERAGE_MIN:-70}"
AI_SERVICE_COVERAGE_MIN="${AI_SERVICE_COVERAGE_MIN:-70}"

cleanup() {
  [[ -n "${GO_COVER_FILE:-}" && -f "${GO_COVER_FILE}" ]] && rm -f "${GO_COVER_FILE}"
  [[ -n "${AI_COVER_LOG:-}" && -f "${AI_COVER_LOG}" ]] && rm -f "${AI_COVER_LOG}"
}
trap cleanup EXIT

is_coverage_ok() {
  local actual="$1"
  local min="$2"
  awk -v actual="$actual" -v min="$min" 'BEGIN { exit (actual + 0 >= min + 0) ? 0 : 1 }'
}

echo "=== Coverage Gate ==="
echo "Minimums: backend/internal >= ${BACKEND_INTERNAL_COVERAGE_MIN}%, ai-service >= ${AI_SERVICE_COVERAGE_MIN}%"

echo ""
echo "=== Go coverage: backend/internal ==="
cd "${ROOT_DIR}/backend"
GO_COVER_FILE="$(mktemp)"
go test ./internal/... -covermode=atomic -coverprofile="${GO_COVER_FILE}" -count=1

GO_TOTAL_RAW="$(go tool cover -func="${GO_COVER_FILE}" | awk '/^total:/ {print $3}')"
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
./scripts/pytest_ai.sh --cov=src --cov-report=term-missing -q | tee "${AI_COVER_LOG}"
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
echo "Coverage gate PASSED"
