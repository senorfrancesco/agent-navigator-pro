#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="${LLM_TOOLS_PLATFORM_BACKEND_ENV_FILE:-$BACKEND_DIR/.env}"
PYTHON_HELPER="$SCRIPT_DIR/download_models.py"

# shellcheck disable=SC1091
source "$PROJECT_ROOT/scripts/utils/env_loader.sh"

MODE="ensure-present"
ASSET_SET="${LLM_TOOLS_PLATFORM_MODEL_ASSET_SET:-core}"
MODELS_ROOT="${LLM_TOOLS_PLATFORM_MODELS_ROOT:-}"
HF_CACHE="${HF_HOME:-${LLM_TOOLS_PLATFORM_HF_CACHE:-}}"
DRY_RUN=0
PYTHON_CMD="$(resolve_env_loader_python || true)"

print_help() {
  cat <<EOF
install_models.sh

Downloads or validates required model artifacts in canonical MODEL_PATH_* locations.

Usage:
  ./scripts/models/install_models.sh --ensure-present
  ./scripts/models/install_models.sh --ensure-present --asset-set=all
  ./scripts/models/install_models.sh --ensure-present --models-root=/mnt/d/agent-models
  HF_HOME=/mnt/d/hf-cache ./scripts/models/install_models.sh --dry-run

Flags:
  --ensure-present                Проверить наличие обязательных артефактов и скачать только отсутствующие.
  --asset-set core|all            Набор моделей: core = LLM + intent + retrieval embeddings,
                                  all = core + VLM + mmproj.
  --models-root /path             Переопределить корневой каталог вместо стандартного ./backend/models.
  --huggingface-cache /path       Переопределить каталог кэша Hugging Face.
  --dry-run                       Показать план путей и источников без скачивания.
  -h, --help                      Показать эту справку.

Examples:
  ./scripts/models/install_models.sh --dry-run
  ./scripts/models/install_models.sh --ensure-present --models-root=/mnt/d/agent-models
  ./scripts/models/install_models.sh --ensure-present --asset-set=all --huggingface-cache=/mnt/d/hf-cache
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    --ensure-present)
      MODE="ensure-present"
      shift
      ;;
    --asset-set)
      ASSET_SET="$2"
      shift 2
      ;;
    --asset-set=*)
      ASSET_SET="${1#*=}"
      shift
      ;;
    --models-root)
      MODELS_ROOT="$2"
      shift 2
      ;;
    --models-root=*)
      MODELS_ROOT="${1#*=}"
      shift
      ;;
    --huggingface-cache)
      HF_CACHE="$2"
      shift 2
      ;;
    --huggingface-cache=*)
      HF_CACHE="${1#*=}"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "Неизвестный аргумент install_models.sh: $1" >&2
      exit 1
      ;;
  esac
done

load_env_file "$ENV_FILE" "backend env" || exit 1

if [ -z "$PYTHON_CMD" ]; then
  echo "Не найден python/python3 для scripts/models/download_models.py" >&2
  exit 1
fi

PY_ARGS=(
  "$PYTHON_HELPER"
  "--mode" "$MODE"
  "--asset-set" "$ASSET_SET"
)

if [ -n "$MODELS_ROOT" ]; then
  PY_ARGS+=("--models-root" "$MODELS_ROOT")
fi

if [ -n "$HF_CACHE" ]; then
  PY_ARGS+=("--huggingface-cache" "$HF_CACHE")
fi

if [ "$DRY_RUN" = "1" ] || [ "${LLM_TOOLS_PLATFORM_TEST_MODE:-0}" = "1" ]; then
  PY_ARGS+=("--dry-run")
fi

exec "$PYTHON_CMD" "${PY_ARGS[@]}"
