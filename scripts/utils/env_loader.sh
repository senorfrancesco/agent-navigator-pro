#!/bin/bash

resolve_env_loader_python() {
  if [ -n "${ENV_LOADER_PYTHON:-}" ] && command -v "${ENV_LOADER_PYTHON}" >/dev/null 2>&1; then
    printf '%s\n' "${ENV_LOADER_PYTHON}"
    return 0
  fi
  if [ -n "${AGENT_NAVIGATOR_ENV_LOADER_PYTHON:-}" ] && command -v "${AGENT_NAVIGATOR_ENV_LOADER_PYTHON}" >/dev/null 2>&1; then
    printf '%s\n' "${AGENT_NAVIGATOR_ENV_LOADER_PYTHON}"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' "python"
    return 0
  fi
  return 1
}

load_env_file() {
  local file="$1"
  local label="${2:-env}"
  local -a env_entries=()
  local loader_python="${ENV_LOADER_PYTHON:-}"

  [ -f "$file" ] || return 0

  if [ ! -r "$file" ]; then
    echo "permission-denied:$label:$file" >&2
    return 1
  fi

  if [ -z "$loader_python" ]; then
    loader_python="$(resolve_env_loader_python)" || {
      echo "python-not-found:env-loader:$file" >&2
      return 1
    }
    ENV_LOADER_PYTHON="$loader_python"
  fi

  mapfile -d '' -t env_entries < <("$loader_python" - "$file" <<'PY'
import sys
from dotenv import dotenv_values

for key, value in dotenv_values(sys.argv[1]).items():
    if not key:
        continue
    sys.stdout.write(f"{key}={'' if value is None else value}\0")
PY
  ) || {
    echo "env-load-failed:$label:$file" >&2
    return 1
  }

  local entry
  for entry in "${env_entries[@]}"; do
    export "$entry"
  done
}
