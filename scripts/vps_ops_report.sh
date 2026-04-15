#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-deploy@185.154.193.193}"
REMOTE_REPO="${REMOTE_REPO:-/home/deploy/app/siteParserForFreelans}"
REMOTE_JSON="${REMOTE_JSON:-/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.json}"
REMOTE_LOG="${REMOTE_LOG:-/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.log}"
REMOTE_BIN_DIR="${REMOTE_BIN_DIR:-/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/bin}"
RUN_REMOTE=1
DO_CLEANUP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --no-refresh)
      RUN_REMOTE=0
      shift
      ;;
    --cleanup-docker)
      DO_CLEANUP=1
      shift
      ;;
    *)
      echo "Usage: $0 [--host user@host] [--no-refresh] [--cleanup-docker]" >&2
      exit 2
      ;;
  esac
done

if [[ "$RUN_REMOTE" -eq 1 ]]; then
  ssh "$HOST" "cd \"$REMOTE_REPO\" && set -a && . ./.env.production && set +a && python3 \"$REMOTE_BIN_DIR/vps_health_report.py\" generate --repo-root \"$REMOTE_REPO\" --output-json \"$REMOTE_JSON\" --append-log \"$REMOTE_LOG\" --notify-telegram >/dev/null"
fi

if [[ "$DO_CLEANUP" -eq 1 ]]; then
  ssh "$HOST" "bash \"$REMOTE_BIN_DIR/vps_safe_docker_cleanup.sh\""
fi

ssh "$HOST" "cat /home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.json" \
  | python3 ./scripts/vps_health_report.py summary --input-json -
