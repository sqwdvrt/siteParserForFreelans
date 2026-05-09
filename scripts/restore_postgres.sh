#!/usr/bin/env bash
set -euo pipefail

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "ERROR: pg_restore not found in PATH"
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: DATABASE_URL=postgres://... $0 /path/to/backup.dump"
  exit 1
fi

DUMP_FILE="$1"
if [[ ! -f "$DUMP_FILE" ]]; then
  echo "ERROR: dump file not found: $DUMP_FILE"
  exit 1
fi

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" ]]; then
  echo "ERROR: DATABASE_URL is not set"
  exit 1
fi

echo "[restore] restoring from: $DUMP_FILE"
pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --dbname="$DB_URL" \
  "$DUMP_FILE"

echo "[restore] done"
