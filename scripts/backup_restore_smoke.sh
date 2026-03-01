#!/usr/bin/env bash
# Backup / restore smoke test.
#
# Validates the full backup → restore cycle against the real application schema:
#   1. Inserts a sentinel row into the real `jobs` table.
#   2. Takes a pg_dump custom-format backup (via backup_postgres.sh).
#   3. Deletes the sentinel row to simulate data loss.
#   4. Restores from the dump (via restore_postgres.sh).
#   5. Verifies the sentinel row is back in the `jobs` table.
#   6. Verifies all expected application tables are present after restore.
#   7. Verifies the `vector` extension is functional (a simple query).
#
# Requires: psql, pg_dump, pg_restore (postgresql-client), DATABASE_URL.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
for bin in psql pg_dump pg_restore mktemp; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $bin" >&2
    exit 1
  fi
done

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" ]]; then
  echo "ERROR: DATABASE_URL is not set" >&2
  exit 1
fi

BACKUP_DIR="$(mktemp -d)"
trap 'rm -rf "$BACKUP_DIR"' EXIT

SENTINEL_URL="https://backup-restore-smoke.local/jobs/$(date -u +%Y%m%dT%H%M%SZ)-$$"

# ---------------------------------------------------------------------------
# Step 1: insert sentinel row into the real `jobs` table
# ---------------------------------------------------------------------------
echo "[backup-restore-smoke] inserting sentinel into jobs table"
SENTINEL_ID="$(psql "$DB_URL" -tA -v ON_ERROR_STOP=1 <<SQL
INSERT INTO jobs (source, url, title, description, raw_html)
VALUES (
  'backup-restore-smoke',
  '${SENTINEL_URL}',
  'Backup Restore Smoke Sentinel',
  'Automated backup/restore smoke test row — safe to delete',
  '<p>backup-restore-smoke</p>'
)
RETURNING id;
SQL
)"
SENTINEL_ID="$(printf '%s' "$SENTINEL_ID" | tr -d '[:space:]')"
if [[ -z "$SENTINEL_ID" ]] || ! [[ "$SENTINEL_ID" =~ ^[0-9]+$ ]]; then
  echo "ERROR: failed to insert sentinel row (got id='${SENTINEL_ID}')" >&2
  exit 1
fi
echo "[backup-restore-smoke] sentinel job_id=${SENTINEL_ID}"

# ---------------------------------------------------------------------------
# Step 2: backup
# ---------------------------------------------------------------------------
echo "[backup-restore-smoke] running backup"
BACKUP_DIR="$BACKUP_DIR" RETENTION_DAYS=1 DATABASE_URL="$DB_URL" \
  ./scripts/backup_postgres.sh

DUMP_FILE="$(ls -1 "$BACKUP_DIR"/*.dump 2>/dev/null | head -n1 || true)"
if [[ -z "$DUMP_FILE" ]]; then
  echo "ERROR: backup_postgres.sh did not produce a .dump file" >&2
  exit 1
fi
SUM_FILE="${DUMP_FILE}.sha256"
if [[ ! -f "$SUM_FILE" ]]; then
  echo "ERROR: checksum file missing: $SUM_FILE" >&2
  exit 1
fi
echo "[backup-restore-smoke] dump: $DUMP_FILE"

# ---------------------------------------------------------------------------
# Step 3: delete the sentinel row to simulate data loss
# ---------------------------------------------------------------------------
echo "[backup-restore-smoke] deleting sentinel to simulate data loss"
psql "$DB_URL" -v ON_ERROR_STOP=1 \
  -c "DELETE FROM jobs WHERE id = ${SENTINEL_ID}"

lost_count="$(psql "$DB_URL" -tA -v ON_ERROR_STOP=1 \
  -c "SELECT COUNT(*) FROM jobs WHERE id = ${SENTINEL_ID}")"
if [[ "$lost_count" != "0" ]]; then
  echo "ERROR: sentinel row was not deleted (count=${lost_count})" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 4: restore — pg_restore exits with 1 on non-fatal warnings; we accept
# that and check the actual data state instead of relying solely on exit code.
# ---------------------------------------------------------------------------
echo "[backup-restore-smoke] restoring from dump"
DATABASE_URL="$DB_URL" ./scripts/restore_postgres.sh "$DUMP_FILE" || {
  restore_exit=$?
  # pg_restore exits 1 for warnings (e.g. "relation does not exist" on --clean
  # drops that are harmless). Exit 2 is a fatal error. Anything ≥2 is a hard
  # failure; exit 1 is acceptable — we validate data state explicitly below.
  if [[ "$restore_exit" -ge 2 ]]; then
    echo "ERROR: restore_postgres.sh exited with fatal code ${restore_exit}" >&2
    exit 1
  fi
  echo "[backup-restore-smoke] restore exited ${restore_exit} (non-fatal warnings accepted)"
}

# ---------------------------------------------------------------------------
# Step 5: verify sentinel row is back
# ---------------------------------------------------------------------------
echo "[backup-restore-smoke] verifying sentinel row restored"
actual_count="$(psql "$DB_URL" -tA -v ON_ERROR_STOP=1 \
  -c "SELECT COUNT(*) FROM jobs WHERE id = ${SENTINEL_ID} AND url = '${SENTINEL_URL}'")"
actual_count="$(printf '%s' "$actual_count" | tr -d '[:space:]')"
if [[ "$actual_count" != "1" ]]; then
  echo "ERROR: sentinel row not found after restore (count=${actual_count})" >&2
  exit 1
fi
echo "[backup-restore-smoke] sentinel row verified"

# ---------------------------------------------------------------------------
# Step 6: verify all expected application tables are present
# ---------------------------------------------------------------------------
echo "[backup-restore-smoke] verifying application schema"
EXPECTED_TABLES=(jobs job_embeddings users notifications pending_ac_jobs)
missing=0
for tbl in "${EXPECTED_TABLES[@]}"; do
  tbl_count="$(psql "$DB_URL" -tA -v ON_ERROR_STOP=1 <<SQL
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = '${tbl}';
SQL
  )"
  tbl_count="$(printf '%s' "$tbl_count" | tr -d '[:space:]')"
  if [[ "$tbl_count" != "1" ]]; then
    echo "  MISSING table: ${tbl}" >&2
    missing=$((missing + 1))
  else
    echo "  OK table: ${tbl}"
  fi
done
if [[ "$missing" -gt 0 ]]; then
  echo "ERROR: ${missing} expected table(s) missing after restore" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 7: verify vector extension is functional
# ---------------------------------------------------------------------------
echo "[backup-restore-smoke] verifying vector extension"
ext_ok="$(psql "$DB_URL" -tA -v ON_ERROR_STOP=1 \
  -c "SELECT extname FROM pg_extension WHERE extname = 'vector'")"
ext_ok="$(printf '%s' "$ext_ok" | tr -d '[:space:]')"
if [[ "$ext_ok" != "vector" ]]; then
  echo "ERROR: vector extension not present after restore" >&2
  exit 1
fi
# Smoke-test that vector arithmetic actually works (no column, just a cast)
psql "$DB_URL" -v ON_ERROR_STOP=1 \
  -c "SELECT '[1,0,0]'::vector(3) <=> '[0,1,0]'::vector(3)" >/dev/null
echo "[backup-restore-smoke] vector extension functional"

# ---------------------------------------------------------------------------
# Cleanup: remove the sentinel row
# ---------------------------------------------------------------------------
psql "$DB_URL" -v ON_ERROR_STOP=1 \
  -c "DELETE FROM jobs WHERE id = ${SENTINEL_ID}" >/dev/null

echo "[backup-restore-smoke] PASSED"
