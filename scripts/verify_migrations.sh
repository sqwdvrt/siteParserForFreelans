#!/bin/bash
# Проверка миграций: применяет все SQL-файлы из backend/migrations и проверяет базовые операции
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # Auto-load repo .env to reduce false failures when DATABASE_URL is not exported.
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" ]]; then
  echo "ERROR: DATABASE_URL is not set."
  echo "Set DATABASE_URL in environment or in $ENV_FILE"
  exit 1
fi

MIGRATIONS_DIR="${MIGRATIONS_DIR:-${ROOT_DIR}/backend/migrations}"

if [[ ! -d "$MIGRATIONS_DIR" ]]; then
  echo "ERROR: migrations directory not found: $MIGRATIONS_DIR"
  exit 1
fi

shopt -s nullglob
migration_files=("$MIGRATIONS_DIR"/*.sql)
shopt -u nullglob

if [[ ${#migration_files[@]} -eq 0 ]]; then
  echo "ERROR: no migration files found in $MIGRATIONS_DIR"
  exit 1
fi

echo "Applying migrations..."
for migration in "${migration_files[@]}"; do
  echo " - $migration"
  psql "$DB_URL" -v ON_ERROR_STOP=1 -f "$migration"
done

echo "Verifying tables..."
psql "$DB_URL" -c "\dt"

echo "Inserting test data..."
psql "$DB_URL" -c "
INSERT INTO jobs (source, url, title, description, raw_html)
VALUES ('kwork', 'https://kwork.ru/projects/1/view', 'Test', 'Test desc', '<p>test</p>')
ON CONFLICT (url) DO NOTHING;
"

# Test vector insert (384 zeros for pgvector)
psql "$DB_URL" -c "
INSERT INTO job_embeddings (job_id, embedding, ai_metadata)
SELECT id, ('[' || array_to_string(array_fill(0.0::float, ARRAY[384]), ',') || ']')::vector(384), '{}'::jsonb
FROM jobs WHERE url = 'https://kwork.ru/projects/1/view'
ON CONFLICT (job_id) DO NOTHING;
"

echo "Verification complete."
psql "$DB_URL" -c "SELECT id, url, title FROM jobs LIMIT 1;"
psql "$DB_URL" -c "SELECT job_id FROM job_embeddings LIMIT 1;"
