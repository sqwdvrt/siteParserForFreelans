#!/usr/bin/env sh
set -eu

TEMPLATE_PATH="${1:-/etc/alertmanager/alertmanager.yml.tmpl}"
OUTPUT_PATH="${2:-/tmp/alertmanager.yml}"
CHAT_ID_FILE="${ALERTMANAGER_TELEGRAM_CHAT_ID_FILE:-/etc/alertmanager/secrets/telegram_chat_id}"
CHAT_ID_ENV="${ALERTMANAGER_TELEGRAM_CHAT_ID:-}"

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "ERROR: alertmanager template not found: $TEMPLATE_PATH" >&2
  exit 1
fi

if [ -n "$CHAT_ID_ENV" ]; then
  CHAT_ID="$CHAT_ID_ENV"
else
  if [ ! -f "$CHAT_ID_FILE" ]; then
    echo "ERROR: telegram chat_id secret file not found: $CHAT_ID_FILE" >&2
    exit 1
  fi
  CHAT_ID="$(tr -d '[:space:]' < "$CHAT_ID_FILE")"
fi

case "$CHAT_ID" in
  ''|'-'|*[!0-9-]*)
    echo "ERROR: ALERTMANAGER_TELEGRAM_CHAT_ID must be integer, got: '$CHAT_ID'" >&2
    exit 1
    ;;
esac

if ! printf '%s' "$CHAT_ID" | grep -Eq '^-?[0-9]+$'; then
  echo "ERROR: ALERTMANAGER_TELEGRAM_CHAT_ID must be integer, got: '$CHAT_ID'" >&2
  exit 1
fi

sed "s/__TELEGRAM_CHAT_ID__/$CHAT_ID/g" "$TEMPLATE_PATH" > "$OUTPUT_PATH"
