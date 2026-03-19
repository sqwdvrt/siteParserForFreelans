#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-${ROOT_DIR}/backend/migrations}"
LIST_MIGRATIONS_SCRIPT="${ROOT_DIR}/backend/scripts/list-migrations.sh"
MIGRATION_RETRY_ATTEMPTS="${MIGRATION_RETRY_ATTEMPTS:-30}"
MIGRATION_RETRY_DELAY_SEC="${MIGRATION_RETRY_DELAY_SEC:-2}"
DATABASE_URL="${DATABASE_URL:-}"

if [[ -z "${DATABASE_URL}" ]]; then
  echo "ERROR: DATABASE_URL is not set." >&2
  exit 1
fi

if [[ ! -d "${MIGRATIONS_DIR}" ]]; then
  echo "ERROR: migrations directory not found: ${MIGRATIONS_DIR}" >&2
  exit 1
fi

if [[ ! -f "${LIST_MIGRATIONS_SCRIPT}" ]]; then
  echo "ERROR: migration lister script was not found: ${LIST_MIGRATIONS_SCRIPT}" >&2
  exit 1
fi

case "${MIGRATION_RETRY_ATTEMPTS}" in
  ''|*[!0-9]*)
    echo "ERROR: MIGRATION_RETRY_ATTEMPTS must be a positive integer, got: ${MIGRATION_RETRY_ATTEMPTS}" >&2
    exit 1
    ;;
esac

case "${MIGRATION_RETRY_DELAY_SEC}" in
  ''|*[!0-9]*)
    echo "ERROR: MIGRATION_RETRY_DELAY_SEC must be a non-negative integer, got: ${MIGRATION_RETRY_DELAY_SEC}" >&2
    exit 1
    ;;
esac

if (( MIGRATION_RETRY_ATTEMPTS <= 0 )); then
  echo "ERROR: MIGRATION_RETRY_ATTEMPTS must be > 0, got: ${MIGRATION_RETRY_ATTEMPTS}" >&2
  exit 1
fi

run_migration_with_retry() {
  local migration_file="$1"
  local attempt=1

  while (( attempt <= MIGRATION_RETRY_ATTEMPTS )); do
    if psql "${DATABASE_URL}" -v ON_ERROR_STOP=1 -f "${migration_file}"; then
      return 0
    fi

    if (( attempt == MIGRATION_RETRY_ATTEMPTS )); then
      echo "Failed after ${attempt}/${MIGRATION_RETRY_ATTEMPTS} attempts: $(basename "${migration_file}")" >&2
      return 1
    fi

    echo "Attempt ${attempt}/${MIGRATION_RETRY_ATTEMPTS} failed: $(basename "${migration_file}"). Retrying in ${MIGRATION_RETRY_DELAY_SEC}s..." >&2
    attempt=$((attempt + 1))
    sleep "${MIGRATION_RETRY_DELAY_SEC}"
  done

  return 1
}

MIGRATION_LIST_FILE="$(mktemp)"
trap 'rm -f "${MIGRATION_LIST_FILE}"' EXIT

sh "${LIST_MIGRATIONS_SCRIPT}" "${MIGRATIONS_DIR}" > "${MIGRATION_LIST_FILE}"

if [[ ! -s "${MIGRATION_LIST_FILE}" ]]; then
  echo "ERROR: no migration files found in ${MIGRATIONS_DIR}" >&2
  exit 1
fi

echo "Applying migrations from ${MIGRATIONS_DIR}..."
while IFS= read -r migration; do
  [[ -n "${migration}" ]] || continue
  echo " - ${migration}"
  run_migration_with_retry "${migration}"
done < "${MIGRATION_LIST_FILE}"

echo "Migrations complete."
