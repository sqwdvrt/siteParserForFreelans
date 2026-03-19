#!/bin/sh
# Emits migration files in the declared execution order.
set -eu

MIGRATIONS_DIR="${1:-}"

if [ -z "$MIGRATIONS_DIR" ]; then
  echo "usage: $0 <migrations-dir>" >&2
  exit 1
fi

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "migrations directory not found: $MIGRATIONS_DIR" >&2
  exit 1
fi

MANIFEST_FILE="$MIGRATIONS_DIR/manifest.txt"

cleanup_files() {
  for file in "$@"; do
    [ -n "$file" ] && [ -f "$file" ] && rm -f "$file"
  done
}

validate_migration_name() {
  migration_name="$1"
  case "$migration_name" in
    [0-9][0-9][0-9]_*.sql)
      return 0
      ;;
    *)
      echo "invalid migration filename (expected NNN_description.sql): $migration_name" >&2
      return 1
      ;;
  esac
}

record_migration_prefix() {
  migration_name="$1"
  migration_prefix="${migration_name%%_*}"
  case " $SEEN_PREFIXES " in
    *" $migration_prefix "*)
      echo "duplicate migration prefix detected: $migration_prefix ($migration_name)" >&2
      return 1
      ;;
    *)
      SEEN_PREFIXES="${SEEN_PREFIXES} ${migration_prefix}"
      return 0
      ;;
  esac
}

if [ -f "$MANIFEST_FILE" ]; then
  listed_entries="$(mktemp)"
  listed_entries_sorted="$(mktemp)"
  discovered_entries="$(mktemp)"
  ordered_paths="$(mktemp)"
  SEEN_PREFIXES=""

  while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in
      ''|\#*)
        continue
        ;;
    esac

    if ! validate_migration_name "$entry"; then
      cleanup_files "$listed_entries" "$listed_entries_sorted" "$discovered_entries" "$ordered_paths"
      exit 1
    fi
    if ! record_migration_prefix "$entry"; then
      cleanup_files "$listed_entries" "$listed_entries_sorted" "$discovered_entries" "$ordered_paths"
      exit 1
    fi

    migration_path="$MIGRATIONS_DIR/$entry"
    if [ ! -f "$migration_path" ]; then
      cleanup_files "$listed_entries" "$listed_entries_sorted" "$discovered_entries" "$ordered_paths"
      echo "manifest references missing migration: $migration_path" >&2
      exit 1
    fi

    printf '%s\n' "$entry" >> "$listed_entries"
    printf '%s\n' "$migration_path" >> "$ordered_paths"
  done < "$MANIFEST_FILE"

  duplicate_entries="$(sort "$listed_entries" | uniq -d || true)"
  if [ -n "$duplicate_entries" ]; then
    cleanup_files "$listed_entries" "$listed_entries_sorted" "$discovered_entries" "$ordered_paths"
    echo "duplicate manifest entries in $MANIFEST_FILE:" >&2
    printf '%s\n' "$duplicate_entries" >&2
    exit 1
  fi

  sort "$listed_entries" > "$listed_entries_sorted"
  find "$MIGRATIONS_DIR" -maxdepth 1 -type f -name '*.sql' -exec basename {} \; | sort > "$discovered_entries"
  if ! diff -u "$listed_entries_sorted" "$discovered_entries" >/dev/null; then
    cleanup_files "$listed_entries" "$listed_entries_sorted" "$discovered_entries" "$ordered_paths"
    echo "manifest does not match SQL files in $MIGRATIONS_DIR" >&2
    exit 1
  fi

  cat "$ordered_paths"
  cleanup_files "$listed_entries" "$listed_entries_sorted" "$discovered_entries" "$ordered_paths"
  exit 0
fi

discovered_entries="$(mktemp)"
find "$MIGRATIONS_DIR" -maxdepth 1 -type f -name '*.sql' -exec basename {} \; | sort > "$discovered_entries"

if [ ! -s "$discovered_entries" ]; then
  cleanup_files "$discovered_entries"
  echo "no SQL migrations found in $MIGRATIONS_DIR" >&2
  exit 1
fi

SEEN_PREFIXES=""
while IFS= read -r entry; do
  if ! validate_migration_name "$entry"; then
    cleanup_files "$discovered_entries"
    exit 1
  fi
  if ! record_migration_prefix "$entry"; then
    cleanup_files "$discovered_entries"
    exit 1
  fi
  printf '%s\n' "$MIGRATIONS_DIR/$entry"
done < "$discovered_entries"

cleanup_files "$discovered_entries"
