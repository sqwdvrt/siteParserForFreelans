#!/usr/bin/env sh
set -eu

CONFIG_PATH="/tmp/alertmanager.yml"
umask 077

/bin/sh /usr/local/bin/render-alertmanager-config.sh \
  /etc/alertmanager/alertmanager.yml.tmpl \
  "$CONFIG_PATH"

# Drop secret env vars after rendering config.
unset ALERTMANAGER_TELEGRAM_BOT_TOKEN ALERTMANAGER_TELEGRAM_CHAT_ID ALERTMANAGER_SLACK_WEBHOOK_URL TELEGRAM_BOT_TOKEN TELEGRAM_ID

exec /bin/alertmanager \
  --config.file="$CONFIG_PATH" \
  --storage.path=/alertmanager
