#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-${ROOT_DIR}/backend/migrations}"
LIST_MIGRATIONS_SCRIPT="${ROOT_DIR}/backend/scripts/list-migrations.sh"

if [[ ! -x "${LIST_MIGRATIONS_SCRIPT}" ]]; then
  echo "ERROR: migration lister script is not executable: ${LIST_MIGRATIONS_SCRIPT}" >&2
  exit 1
fi

"${LIST_MIGRATIONS_SCRIPT}" "${MIGRATIONS_DIR}" >/dev/null
echo "Migration filenames OK: ${MIGRATIONS_DIR}"
