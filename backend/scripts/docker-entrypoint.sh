#!/bin/sh
# Runs migrations then execs the main process.
set -e

if [ -z "${API_ADDR:-}" ] && [ -n "${PORT:-}" ]; then
  export API_ADDR=":${PORT}"
  echo "API_ADDR is not set; using PORT=${PORT}"
fi

if [ -n "$DATABASE_URL" ] && [ -d /app/migrations ]; then
  echo "Running migrations..."
  for f in /app/migrations/*.sql; do
    [ -f "$f" ] || continue
    echo "  Applying $(basename "$f")..."
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
  done
  echo "Migrations complete."
fi

exec "$@"
