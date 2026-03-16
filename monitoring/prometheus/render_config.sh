#!/usr/bin/env sh
set -eu

TEMPLATE_PATH="${1:-/etc/prometheus/prometheus.yml.tmpl}"
OUTPUT_PATH="${2:-/tmp/prometheus.yml}"
BACKEND_API_METRICS_TARGET="${BACKEND_API_METRICS_TARGET:-backend-api:8080}"
BACKEND_API_METRICS_SCHEME="${BACKEND_API_METRICS_SCHEME:-http}"
BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY="${BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY:-false}"

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "ERROR: prometheus template not found: $TEMPLATE_PATH" >&2
  exit 1
fi

case "$BACKEND_API_METRICS_SCHEME" in
  http|https) ;;
  *)
    echo "ERROR: BACKEND_API_METRICS_SCHEME must be http or https, got: '$BACKEND_API_METRICS_SCHEME'" >&2
    exit 1
    ;;
esac

case "$BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY" in
  true|false) ;;
  *)
    echo "ERROR: BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY must be true or false, got: '$BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY'" >&2
    exit 1
    ;;
esac

escape_awk_replacement() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g'
}

BACKEND_API_METRICS_TARGET_ESCAPED="$(escape_awk_replacement "$BACKEND_API_METRICS_TARGET")"
BACKEND_API_METRICS_SCHEME_ESCAPED="$(escape_awk_replacement "$BACKEND_API_METRICS_SCHEME")"
BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY_ESCAPED="$(escape_awk_replacement "$BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY")"

awk \
  -v backend_api_metrics_target="$BACKEND_API_METRICS_TARGET_ESCAPED" \
  -v backend_api_metrics_scheme="$BACKEND_API_METRICS_SCHEME_ESCAPED" \
  -v backend_api_metrics_tls_insecure_skip_verify="$BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY_ESCAPED" \
  -v backend_api_metrics_scheme_raw="$BACKEND_API_METRICS_SCHEME" \
  '
  /__BACKEND_API_TLS_CONFIG_START__/ { in_backend_api_tls_config=1; next }
  /__BACKEND_API_TLS_CONFIG_END__/ { in_backend_api_tls_config=0; next }
  {
    if (in_backend_api_tls_config && backend_api_metrics_scheme_raw != "https") {
      next
    }
    gsub(/__BACKEND_API_METRICS_TARGET__/, backend_api_metrics_target)
    gsub(/__BACKEND_API_METRICS_SCHEME__/, backend_api_metrics_scheme)
    gsub(/__BACKEND_API_METRICS_TLS_INSECURE_SKIP_VERIFY__/, backend_api_metrics_tls_insecure_skip_verify)
    print
  }
  ' \
  "$TEMPLATE_PATH" > "$OUTPUT_PATH"
