#!/usr/bin/env sh
set -eu

/bin/sh /usr/local/bin/render-alertmanager-config.sh \
  /etc/alertmanager/alertmanager.yml.tmpl \
  /tmp/alertmanager.yml

exec /bin/alertmanager \
  --config.file=/tmp/alertmanager.yml \
  --storage.path=/alertmanager
