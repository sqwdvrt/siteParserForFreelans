#!/bin/bash
# Проверка миграций: применяет 001_init.sql и проверяет таблицы
set -e

DB_URL="${DATABASE_URL:-postgres://site_parser:site_parser@localhost:5432/site_parser?sslmode=disable}"

echo "Applying migrations..."
psql "$DB_URL" -f backend/migrations/001_init.sql

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
