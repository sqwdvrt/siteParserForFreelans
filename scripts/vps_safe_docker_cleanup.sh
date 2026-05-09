#!/usr/bin/env bash
set -euo pipefail

DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-80}"
ROOT_MOUNT="${ROOT_MOUNT:-/}"

current_used_percent() {
  df -Pk "${ROOT_MOUNT}" | awk 'NR==2 {gsub(/%/, "", $5); print $5}'
}

before="$(current_used_percent)"
echo "[vps-cleanup] disk usage before cleanup: ${before}%"

if [[ "${before}" -lt "${DISK_WARN_PERCENT}" ]]; then
  echo "[vps-cleanup] below threshold ${DISK_WARN_PERCENT}%, skipping cleanup"
  exit 0
fi

docker image prune -f
docker builder prune -f

after="$(current_used_percent)"
echo "[vps-cleanup] disk usage after cleanup: ${after}%"
