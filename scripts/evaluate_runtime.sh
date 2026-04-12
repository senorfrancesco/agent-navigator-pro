#!/bin/bash

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="${AGENT_NAVIGATOR_BACKEND_ENV_FILE:-$BACKEND_DIR/.env}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/utils/env_loader.sh"

print_help() {
  cat <<EOF
evaluate_runtime.sh

Явный запуск оценки runtime и плана размещения моделей.
Этот скрипт не запускает сервисы и ничего не записывает автоматически.
Он показывает рекомендации для ручного редактирования backend/.env.

Использование:
  ./scripts/evaluate_runtime.sh
  ./scripts/evaluate_runtime.sh recommend
  ./scripts/evaluate_runtime.sh plan
  ./scripts/evaluate_runtime.sh detect
  ./scripts/evaluate_runtime.sh report

Примеры:
  ./scripts/evaluate_runtime.sh
  ./scripts/evaluate_runtime.sh recommend --profile adaptive
  ./scripts/evaluate_runtime.sh plan --profile adaptive
  ./scripts/evaluate_runtime.sh plan --llm-device-mode gpu --intent-embedder-device-mode cpu
  ./scripts/evaluate_runtime.sh detect
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  print_help
  exit 0
fi

if [ $# -eq 0 ]; then
  set -- recommend
fi

if [ "${1:-}" = "apply" ]; then
  echo "evaluate_runtime.sh не применяет изменения автоматически." >&2
  echo "Используйте ./scripts/evaluate_runtime.sh recommend и перенесите значения в backend/.env вручную." >&2
  exit 1
fi

load_env_file "$ENV_FILE" "backend env" || {
  echo "env-load-failed:backend env:$ENV_FILE" >&2
  exit 1
}

ENV_LOADER_PYTHON="$(resolve_env_loader_python || true)"
if [ -z "$ENV_LOADER_PYTHON" ]; then
  echo "python-not-found:runtime-preflight" >&2
  exit 1
fi

exec "$ENV_LOADER_PYTHON" "$SCRIPT_DIR/runtime_preflight.py" "$@"
