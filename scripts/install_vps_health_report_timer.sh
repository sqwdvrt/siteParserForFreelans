#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SHARED_OPS_BIN="${SHARED_OPS_BIN:-/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/bin}"
REPO_ROOT="${REPO_ROOT:-/home/deploy/app/siteParserForFreelans}"
SHARED_OPS_DIR="${SHARED_OPS_DIR:-/home/deploy/app/.siteParserForFreelans-deploy/shared/ops}"
CRON_TAG="# siteparser-vps-health-report"

install_runtime_files() {
  install -D -m 0755 \
    "${ROOT_DIR}/scripts/vps_health_report.py" \
    "${SHARED_OPS_BIN}/vps_health_report.py"
  install -D -m 0755 \
    "${ROOT_DIR}/scripts/vps_safe_docker_cleanup.sh" \
    "${SHARED_OPS_BIN}/vps_safe_docker_cleanup.sh"
}

install_systemd_timer() {
  install -D -m 0644 \
    "${ROOT_DIR}/ops/systemd/siteparser-vps-health-report.service" \
    "${SYSTEMD_DIR}/siteparser-vps-health-report.service"
  install -D -m 0644 \
    "${ROOT_DIR}/ops/systemd/siteparser-vps-health-report.timer" \
    "${SYSTEMD_DIR}/siteparser-vps-health-report.timer"

  systemctl daemon-reload
  systemctl enable --now siteparser-vps-health-report.timer
}

install_user_cron() {
  local cron_cmd cron_line existing
  cron_cmd="cd \"${REPO_ROOT}\" && set -a && . ./.env.production && set +a && python3 \"${SHARED_OPS_BIN}/vps_health_report.py\" generate --repo-root \"${REPO_ROOT}\" --output-json \"${SHARED_OPS_DIR}/vps-health-report.json\" --append-log \"${SHARED_OPS_DIR}/vps-health-report.log\" --notify-telegram >/dev/null 2>&1"
  cron_line="*/5 * * * * ${cron_cmd} ${CRON_TAG}"
  existing="$(crontab -l 2>/dev/null || true)"
  existing="$(printf '%s\n' "${existing}" | grep -vF "${CRON_TAG}" || true)"
  printf '%s\n%s\n' "${existing}" "${cron_line}" | crontab -
}

install_runtime_files

if [[ "${EUID}" -eq 0 ]]; then
  install_systemd_timer
else
  install_user_cron
fi
