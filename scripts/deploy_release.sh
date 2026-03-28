#!/usr/bin/env bash
set -euo pipefail

BASE_PATH=""
DEPLOY_SHA=""
ENV_FILE=".env.production"
ORIGIN_URL=""
POST_DEPLOY_GATE=""
COMPOSE_FILES=("docker-compose.prod.yml" "docker-compose.ssl.yml")

usage() {
  cat <<'EOF'
Usage: deploy_release.sh --base-path PATH --sha SHA [options]

Options:
  --base-path PATH         Stable live deploy path (required).
  --sha SHA                Commit SHA to deploy (required).
  --env-file NAME          Shared env file name inside releases (default: .env.production).
  --compose-file FILE      Compose file to pass to docker compose. Repeatable.
  --origin-url URL         Optional git origin for first-time repo cache bootstrap.
  --post-deploy-gate PATH  Optional post-deploy gate script to run from the live symlink path.
  --help                   Show this help text.
EOF
}

log() {
  printf '[deploy-release] %s\n' "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

merge_legacy_directory() {
  local source_dir="$1"
  local target_dir="$2"

  [[ -d "$source_dir" ]] || return 0

  mkdir -p "$target_dir"
  shopt -s dotglob nullglob
  for entry in "$source_dir"/*; do
    mv "$entry" "$target_dir"/
  done
  shopt -u dotglob nullglob
  rmdir "$source_dir" 2>/dev/null || true
}

update_symlink() {
  local target="$1"
  local link_path="$2"
  local tmp_link="${link_path}.tmp.$$"

  ln -sfn "$target" "$tmp_link"
  mv -Tf "$tmp_link" "$link_path"
}

require_command() {
  local bin="$1"
  if ! command -v "$bin" >/dev/null 2>&1; then
    die "required command is missing: $bin"
  fi
}

sanitize_project_name() {
  printf '%s' "$1" | tr -c '[:alnum:]_.-' '-'
}

parse_args() {
  local compose_files_set=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --base-path)
        [[ $# -ge 2 ]] || die "--base-path requires a value"
        BASE_PATH="$2"
        shift 2
        ;;
      --sha)
        [[ $# -ge 2 ]] || die "--sha requires a value"
        DEPLOY_SHA="$2"
        shift 2
        ;;
      --env-file)
        [[ $# -ge 2 ]] || die "--env-file requires a value"
        ENV_FILE="$2"
        shift 2
        ;;
      --compose-file)
        [[ $# -ge 2 ]] || die "--compose-file requires a value"
        if [[ "$compose_files_set" -eq 0 ]]; then
          COMPOSE_FILES=()
          compose_files_set=1
        fi
        COMPOSE_FILES+=("$2")
        shift 2
        ;;
      --origin-url)
        [[ $# -ge 2 ]] || die "--origin-url requires a value"
        ORIGIN_URL="$2"
        shift 2
        ;;
      --post-deploy-gate)
        [[ $# -ge 2 ]] || die "--post-deploy-gate requires a value"
        POST_DEPLOY_GATE="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done

  [[ -n "$BASE_PATH" ]] || die "--base-path is required"
  [[ -n "$DEPLOY_SHA" ]] || die "--sha is required"
  [[ "${#COMPOSE_FILES[@]}" -gt 0 ]] || die "at least one --compose-file is required"
}

init_layout() {
  local base_dir_input
  local base_name

  base_dir_input="$(dirname "$BASE_PATH")"
  mkdir -p "$base_dir_input"
  base_dir_input="$(cd "$base_dir_input" && pwd -P)"
  base_name="$(basename "$BASE_PATH")"

  BASE_PATH="${base_dir_input}/${base_name}"
  STATE_DIR="${base_dir_input}/.${base_name}-deploy"
  REPO_DIR="${STATE_DIR}/repo"
  RELEASES_DIR="${STATE_DIR}/releases"
  SHARED_DIR="${STATE_DIR}/shared"
  CURRENT_LINK="${STATE_DIR}/current"
  RELEASE_DIR="${RELEASES_DIR}/${DEPLOY_SHA}"
  SHARED_ENV_FILE="${SHARED_DIR}/${ENV_FILE}"
  SHARED_BACKUPS_DIR="${SHARED_DIR}/backups"
  COMPOSE_PROJECT_NAME="$(sanitize_project_name "$base_name")"

  mkdir -p "$STATE_DIR" "$RELEASES_DIR" "$SHARED_DIR"
}

migrate_legacy_checkout_if_needed() {
  if [[ -L "$BASE_PATH" ]]; then
    return
  fi

  if [[ -d "$BASE_PATH" && -d "$BASE_PATH/.git" ]]; then
    [[ ! -e "$REPO_DIR" ]] || die "repo cache already exists at ${REPO_DIR}; cannot migrate legacy checkout from ${BASE_PATH}"

    log "migrating legacy checkout at ${BASE_PATH} into ${REPO_DIR}"

    if [[ -f "${BASE_PATH}/${ENV_FILE}" && ! -e "$SHARED_ENV_FILE" ]]; then
      mv "${BASE_PATH}/${ENV_FILE}" "$SHARED_ENV_FILE"
      log "moved ${ENV_FILE} into shared state"
    fi

    if [[ -d "${BASE_PATH}/backups" ]]; then
      merge_legacy_directory "${BASE_PATH}/backups" "$SHARED_BACKUPS_DIR"
      log "moved backups into shared state"
    fi

    mv "$BASE_PATH" "$REPO_DIR"
    return
  fi

  if [[ -e "$BASE_PATH" ]]; then
    die "${BASE_PATH} exists but is not a legacy git checkout or symlink"
  fi
}

prepare_repo_cache() {
  if [[ ! -e "$REPO_DIR" ]]; then
    [[ -n "$ORIGIN_URL" ]] || die "repo cache missing at ${REPO_DIR}; rerun with --origin-url or migrate an existing checkout first"
    log "bootstrapping repo cache from ${ORIGIN_URL}"
    git clone "$ORIGIN_URL" "$REPO_DIR"
  fi

  [[ -d "${REPO_DIR}/.git" ]] || die "repo cache is not a git checkout: ${REPO_DIR}"

  git -C "$REPO_DIR" fetch --all --prune --tags
  git -C "$REPO_DIR" rev-parse --verify "${DEPLOY_SHA}^{commit}" >/dev/null 2>&1 || \
    die "target commit is not available in repo cache: ${DEPLOY_SHA}"
}

ensure_shared_state() {
  mkdir -p "$SHARED_BACKUPS_DIR"
  [[ -f "$SHARED_ENV_FILE" ]] || die "shared env file is missing: ${SHARED_ENV_FILE}"
}

ensure_release_worktree() {
  if [[ -e "$RELEASE_DIR" ]]; then
    [[ -d "$RELEASE_DIR" ]] || die "release path exists and is not a directory: ${RELEASE_DIR}"
    [[ -e "${RELEASE_DIR}/.git" ]] || die "release path exists but is not a git worktree: ${RELEASE_DIR}"

    local existing_sha
    existing_sha="$(git -C "$RELEASE_DIR" rev-parse HEAD)"
    [[ "$existing_sha" = "$DEPLOY_SHA" ]] || die "release path ${RELEASE_DIR} already exists for ${existing_sha}, expected ${DEPLOY_SHA}"

    log "reusing existing release worktree ${RELEASE_DIR}"
    return
  fi

  log "creating release worktree ${RELEASE_DIR}"
  git -C "$REPO_DIR" worktree add --detach "$RELEASE_DIR" "$DEPLOY_SHA"
}

link_shared_state() {
  rm -f "${RELEASE_DIR}/${ENV_FILE}"
  ln -s "$SHARED_ENV_FILE" "${RELEASE_DIR}/${ENV_FILE}"

  rm -rf "${RELEASE_DIR}/backups"
  ln -s "$SHARED_BACKUPS_DIR" "${RELEASE_DIR}/backups"

  cat > "${RELEASE_DIR}/.env" <<EOF
COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME}
EOF
}

compose_release() {
  local compose_args=()
  local compose_file

  for compose_file in "${COMPOSE_FILES[@]}"; do
    compose_args+=(-f "$compose_file")
  done

  log "validating env and starting compose for ${DEPLOY_SHA}"
  (
    cd "$RELEASE_DIR"
    bash ./scripts/validate-env-production.sh "$ENV_FILE"
    docker compose --env-file "$ENV_FILE" "${compose_args[@]}" up -d --build
    docker compose --env-file "$ENV_FILE" "${compose_args[@]}" ps
  )
}

switch_live_release() {
  update_symlink "$RELEASE_DIR" "$CURRENT_LINK"
  update_symlink "$CURRENT_LINK" "$BASE_PATH"
  log "live symlink now points to ${DEPLOY_SHA}"
}

run_post_deploy_gate() {
  [[ -n "$POST_DEPLOY_GATE" ]] || return 0

  log "running post-deploy gate ${POST_DEPLOY_GATE}"
  (
    cd "$BASE_PATH"
    set -a
    . "./${ENV_FILE}"
    set +a
    bash "./${POST_DEPLOY_GATE}"
  )
}

main() {
  require_command git
  require_command docker
  require_command mv
  require_command ln
  require_command bash

  parse_args "$@"
  init_layout
  migrate_legacy_checkout_if_needed
  prepare_repo_cache
  ensure_shared_state
  ensure_release_worktree
  link_shared_state
  compose_release
  switch_live_release
  run_post_deploy_gate
}

main "$@"
