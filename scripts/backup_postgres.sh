#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERROR: pg_dump not found in PATH"
  exit 1
fi

if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
  echo "ERROR: sha256sum/shasum not found in PATH"
  exit 1
fi

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" ]]; then
  echo "ERROR: DATABASE_URL is not set"
  exit 1
fi

BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

OUT_FILE="$BACKUP_DIR/site_parser_${TS}.dump"
SUM_FILE="${OUT_FILE}.sha256"

echo "[backup] creating dump: $OUT_FILE"
pg_dump --format=custom --no-owner --no-privileges --dbname="$DB_URL" --file="$OUT_FILE"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$OUT_FILE" > "$SUM_FILE"
else
  shasum -a 256 "$OUT_FILE" > "$SUM_FILE"
fi

echo "[backup] checksum: $SUM_FILE"
echo "[backup] pruning files older than ${RETENTION_DAYS} days in $BACKUP_DIR"
find "$BACKUP_DIR" -type f \( -name '*.dump' -o -name '*.sha256' \) -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] done"
