#!/usr/bin/env sh
set -eu

TEMPLATE_PATH="${1:-/etc/alertmanager/alertmanager.yml.tmpl}"
OUTPUT_PATH="${2:-/tmp/alertmanager.yml}"
CHAT_ID_ENV="${ALERTMANAGER_TELEGRAM_CHAT_ID:-${TELEGRAM_ID:-}}"
BOT_TOKEN_ENV="${ALERTMANAGER_TELEGRAM_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
SLACK_URL_ENV="${ALERTMANAGER_SLACK_WEBHOOK_URL:-}"

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "ERROR: alertmanager template not found: $TEMPLATE_PATH" >&2
  exit 1
fi

CHAT_ID="$CHAT_ID_ENV"
CHAT_ID="$(printf '%s' "$CHAT_ID" | tr -d '[:space:]')"
if [ -z "$CHAT_ID" ]; then
  echo "ERROR: ALERTMANAGER_TELEGRAM_CHAT_ID (or TELEGRAM_ID) must be set" >&2
  exit 1
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

BOT_TOKEN="$BOT_TOKEN_ENV"
BOT_TOKEN="$(printf '%s' "$BOT_TOKEN" | tr -d '\r\n')"
if [ -z "$BOT_TOKEN" ]; then
  echo "ERROR: ALERTMANAGER_TELEGRAM_BOT_TOKEN (or TELEGRAM_BOT_TOKEN) must be set" >&2
  exit 1
fi

SLACK_WEBHOOK_URL="$SLACK_URL_ENV"
SLACK_WEBHOOK_URL="$(printf '%s' "$SLACK_WEBHOOK_URL" | tr -d '\r\n')"
INCLUDE_SLACK=0
if [ -n "$SLACK_WEBHOOK_URL" ]; then
  INCLUDE_SLACK=1
  case "$SLACK_WEBHOOK_URL" in
    https://*) ;;
    *)
      echo "ERROR: ALERTMANAGER_SLACK_WEBHOOK_URL must start with https:// if set" >&2
      exit 1
      ;;
  esac
fi

escape_awk_replacement() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g'
}

CHAT_ID_ESCAPED="$(escape_awk_replacement "$CHAT_ID")"
BOT_TOKEN_ESCAPED="$(escape_awk_replacement "$BOT_TOKEN")"
SLACK_WEBHOOK_URL_ESCAPED="$(escape_awk_replacement "$SLACK_WEBHOOK_URL")"

awk \
  -v chat_id="$CHAT_ID_ESCAPED" \
  -v bot_token="$BOT_TOKEN_ESCAPED" \
  -v slack_webhook_url="$SLACK_WEBHOOK_URL_ESCAPED" \
  -v include_slack="$INCLUDE_SLACK" \
  '
  /__SLACK_ROUTE_START__/ { in_slack_route=1; next }
  /__SLACK_ROUTE_END__/ { in_slack_route=0; next }
  /__SLACK_RECEIVER_START__/ { in_slack_receiver=1; next }
  /__SLACK_RECEIVER_END__/ { in_slack_receiver=0; next }
  {
    if ((in_slack_route || in_slack_receiver) && include_slack != "1") {
      next
    }
    gsub(/__TELEGRAM_CHAT_ID__/, chat_id)
    gsub(/__TELEGRAM_BOT_TOKEN__/, bot_token)
    gsub(/__SLACK_WEBHOOK_URL__/, slack_webhook_url)
    print
  }
  ' \
  "$TEMPLATE_PATH" > "$OUTPUT_PATH"
