#!/bin/sh
# Optionally runs migrations then execs the main process.
set -e

MIGRATION_RETRY_ATTEMPTS="${MIGRATION_RETRY_ATTEMPTS:-30}"
MIGRATION_RETRY_DELAY_SEC="${MIGRATION_RETRY_DELAY_SEC:-2}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-0}"

case "$MIGRATION_RETRY_ATTEMPTS" in
  ''|*[!0-9]*)
    echo "MIGRATION_RETRY_ATTEMPTS must be a positive integer, got: $MIGRATION_RETRY_ATTEMPTS"
    exit 1
    ;;
esac
if [ "$MIGRATION_RETRY_ATTEMPTS" -le 0 ]; then
  echo "MIGRATION_RETRY_ATTEMPTS must be > 0, got: $MIGRATION_RETRY_ATTEMPTS"
  exit 1
fi

case "$MIGRATION_RETRY_DELAY_SEC" in
  ''|*[!0-9]*)
    echo "MIGRATION_RETRY_DELAY_SEC must be a non-negative integer, got: $MIGRATION_RETRY_DELAY_SEC"
    exit 1
    ;;
esac

should_run_migrations() {
  case "$RUN_MIGRATIONS" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    0|false|FALSE|no|NO|off|OFF|'')
      return 1
      ;;
    *)
      echo "RUN_MIGRATIONS must be boolean (accepted: 1/0, true/false, yes/no, on/off), got: $RUN_MIGRATIONS"
      exit 1
      ;;
  esac
}

run_migration_with_retry() {
  migration_file="$1"
  attempt=1
  while [ "$attempt" -le "$MIGRATION_RETRY_ATTEMPTS" ]; do
    if psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration_file"; then
      return 0
    fi

    if [ "$attempt" -eq "$MIGRATION_RETRY_ATTEMPTS" ]; then
      echo "  Failed after ${attempt}/${MIGRATION_RETRY_ATTEMPTS} attempts: $(basename "$migration_file")"
      return 1
    fi

    echo "  Attempt ${attempt}/${MIGRATION_RETRY_ATTEMPTS} failed: $(basename "$migration_file"). Retrying in ${MIGRATION_RETRY_DELAY_SEC}s..."
    attempt=$((attempt + 1))
    sleep "$MIGRATION_RETRY_DELAY_SEC"
  done

  return 1
}

if [ -z "${API_ADDR:-}" ] && [ -n "${PORT:-}" ]; then
  export API_ADDR=":${PORT}"
  echo "API_ADDR is not set; using PORT=${PORT}"
fi

if should_run_migrations; then
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "RUN_MIGRATIONS is enabled but DATABASE_URL is empty"
    exit 1
  fi
  if [ ! -d /app/migrations ]; then
    echo "RUN_MIGRATIONS is enabled but /app/migrations does not exist"
    exit 1
  fi

  echo "Running migrations (attempts=${MIGRATION_RETRY_ATTEMPTS}, delay=${MIGRATION_RETRY_DELAY_SEC}s)..."
  for f in /app/migrations/*.sql; do
    [ -f "$f" ] || continue
    echo "  Applying $(basename "$f")..."
    run_migration_with_retry "$f"
  done
  echo "Migrations complete."
fi

exec "$@"
