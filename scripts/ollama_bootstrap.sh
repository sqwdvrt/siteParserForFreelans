#!/bin/sh
set -eu

OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"
export OLLAMA_HOST

models=""

append_model() {
  model="$1"
  [ -n "$model" ] || return 0
  case " $models " in
    *" $model "*) return 0 ;;
  esac
  models="$models $model"
}

append_model "${OLLAMA_MODEL:-llama3.2:3b-instruct-q4_K_M}"
append_model "${ACTOR_OLLAMA_MODEL:-${OLLAMA_MODEL:-llama3.2:3b-instruct-q4_K_M}}"
append_model "${CRITIC_OLLAMA_MODEL:-${OLLAMA_MODEL:-llama3.2:3b-instruct-q4_K_M}}"

for attempt in $(seq 1 60); do
  if ollama list >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "ollama bootstrap: daemon did not become ready" >&2
    exit 1
  fi
  sleep 2
done

for model in $models; do
  echo "ollama bootstrap: ensuring model $model"
  ollama pull "$model"
done
