#!/bin/sh
set -eu

case "${BACKEND_ROLE:-api}" in
  api)
    cmd="./api"
    ;;
  crawler)
    export CRAWLER_HEALTH_ADDR=":${PORT:-8081}"
    cmd="./crawler"
    ;;
  notifier)
    export NOTIFIER_HEALTH_ADDR=":${PORT:-8082}"
    cmd="./notifier"
    ;;
  *)
    echo "unknown BACKEND_ROLE=${BACKEND_ROLE:-}" >&2
    exit 1
    ;;
esac

if [ "${BACKEND_ENTRYPOINT_ACTIVE:-0}" = "1" ]; then
  exec "$cmd"
fi

exec sh ./scripts/docker-entrypoint.sh "$cmd"
