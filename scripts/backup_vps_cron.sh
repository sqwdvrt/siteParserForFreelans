#!/usr/bin/env bash
# backup_vps_cron.sh — запускать из cron на VPS.
# Использует docker exec на infra-postgres-1 (pg_dump уже есть внутри).
# Бэкап складывается в shared/backups/, ротация 14 дней.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/home/deploy/app/.siteParserForFreelans-deploy/shared/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
PG_CONTAINER="${PG_CONTAINER:-infra-postgres-1}"
PG_USER="${PG_USER:-site_parser}"
PG_DBNAME="${PG_DBNAME:-site_parser}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="${BACKUP_DIR}/site_parser_${TS}.dump"
SUM_FILE="${OUT_FILE}.sha256"
LOG_PREFIX="[backup $(date -u +%Y-%m-%dT%H:%M:%SZ)]"

mkdir -p "${BACKUP_DIR}"

echo "${LOG_PREFIX} starting dump -> ${OUT_FILE}"
docker exec "${PG_CONTAINER}" \
  pg_dump -U "${PG_USER}" --format=custom "${PG_DBNAME}" \
  > "${OUT_FILE}"

echo "${LOG_PREFIX} writing checksum"
sha256sum "${OUT_FILE}" > "${SUM_FILE}"

echo "${LOG_PREFIX} pruning files older than ${RETENTION_DAYS} days"
find "${BACKUP_DIR}" -type f \( -name '*.dump' -o -name '*.sha256' \) \
  -mtime "+${RETENTION_DAYS}" -delete

SIZE=$(du -sh "${OUT_FILE}" | cut -f1)
echo "${LOG_PREFIX} done: ${OUT_FILE} (${SIZE})"
