#!/usr/bin/env sh
set -eu

if [ "${AI_COMBINE_SSL_CERT_FILE:-0}" = "1" ] && [ -n "${SSL_CERT_FILE:-}" ] && [ -r "${SSL_CERT_FILE}" ]; then
  public_ca="$("${VENV_PATH:-/opt/venv}/bin/python" -c 'import certifi; print(certifi.where())')"
  combined_ca="${AI_COMBINED_SSL_CERT_FILE:-/tmp/combined-ca-certificates.crt}"
  cat "${public_ca}" "${SSL_CERT_FILE}" > "${combined_ca}"
  export SSL_CERT_FILE="${combined_ca}"
  export REQUESTS_CA_BUNDLE="${combined_ca}"
fi

exec "$@"
