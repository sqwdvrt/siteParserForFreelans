#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"

MODE="${BACKEND_GO_TEST_MODE:-auto}" # auto|host|docker
HOST_GOTOOLCHAIN="${BACKEND_HOST_GOTOOLCHAIN:-${GO_TOOLCHAIN:-go1.25.8}}"
HOST_GOMODCACHE="${BACKEND_GOMODCACHE:-${GO_GOMODCACHE:-${BACKEND_DIR}/.gomodcache-${HOST_GOTOOLCHAIN}}}"
HOST_GOCACHE="${BACKEND_GOCACHE:-${GO_GOCACHE:-${BACKEND_DIR}/.gocache-${HOST_GOTOOLCHAIN}}}"

DOCKER_IMAGE="${BACKEND_GO_DOCKER_IMAGE:-golang:1.25}"
DOCKER_GOMODCACHE_REL=".gomodcache-docker"
DOCKER_GOCACHE_REL=".gocache-docker"

if [[ $# -eq 0 ]]; then
  set -- ./...
fi

run_host() {
  echo "[go-test] running on host (GOTOOLCHAIN=${HOST_GOTOOLCHAIN})"
  mkdir -p "${HOST_GOMODCACHE}" "${HOST_GOCACHE}"
  (
    cd "${BACKEND_DIR}" && \
      GOTOOLCHAIN="${HOST_GOTOOLCHAIN}" \
      GOMODCACHE="${HOST_GOMODCACHE}" \
      GOCACHE="${HOST_GOCACHE}" \
      go test "$@"
  )
}

run_docker() {
  echo "[go-test] running in docker (${DOCKER_IMAGE})"
  mkdir -p "${BACKEND_DIR}/${DOCKER_GOMODCACHE_REL}" "${BACKEND_DIR}/${DOCKER_GOCACHE_REL}"
  docker run --rm \
    -u "$(id -u):$(id -g)" \
    -v "${ROOT_DIR}:/work" \
    -w /work/backend \
    -e GOTOOLCHAIN=local \
    -e GOMODCACHE="/work/backend/${DOCKER_GOMODCACHE_REL}" \
    -e GOCACHE="/work/backend/${DOCKER_GOCACHE_REL}" \
    -e GOPROXY="${GOPROXY:-https://proxy.golang.org,direct}" \
    -e GOSUMDB="${GOSUMDB:-sum.golang.org}" \
    "${DOCKER_IMAGE}" \
    go test "$@"
}

case "${MODE}" in
  host)
    run_host "$@"
    ;;
  docker)
    run_docker "$@"
    ;;
  auto)
    if ! run_host "$@"; then
      echo "[go-test] host run failed, retrying in docker"
      run_docker "$@"
    fi
    ;;
  *)
    echo "ERROR: unsupported BACKEND_GO_TEST_MODE='${MODE}' (expected: auto|host|docker)" >&2
    exit 1
    ;;
esac
