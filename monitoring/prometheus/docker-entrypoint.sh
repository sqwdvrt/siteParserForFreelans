#!/usr/bin/env sh
set -eu

CONFIG_PATH="/tmp/prometheus.yml"
umask 077

/bin/sh /usr/local/bin/render-prometheus-config.sh \
  /etc/prometheus/prometheus.yml.tmpl \
  "$CONFIG_PATH"

exec /bin/prometheus \
  --config.file="$CONFIG_PATH" \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=30d \
  --storage.tsdb.retention.size=4GB \
  --web.enable-lifecycle
