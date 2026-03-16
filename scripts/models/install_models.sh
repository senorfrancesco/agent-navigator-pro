#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="${AGENT_NAVIGATOR_BACKEND_ENV_FILE:-$BACKEND_DIR/.env}"
NATIVE_ENV_FILE="${AGENT_NAVIGATOR_BACKEND_NATIVE_ENV_FILE:-$BACKEND_DIR/.env.native}"
PYTHON_HELPER="$SCRIPT_DIR/download_models.py"

MODE="ensure-present"
ASSET_SET="${AGENT_NAVIGATOR_MODEL_ASSET_SET:-core}"
MODELS_ROOT="${AGENT_NAVIGATOR_MODELS_ROOT:-}"
HF_CACHE="${HF_HOME:-${AGENT_NAVIGATOR_HF_CACHE:-}}"
DRY_RUN=0

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
  --ensure-present                Ensure required artifacts exist; download only missing ones.
  --asset-set core|all            core = LLM + intent + retrieval embeddings, all = core + VLM + mmproj
  --models-root /path             Override default ./backend/models root for downloader targets.
  --huggingface-cache /path       Override Hugging Face cache location.
  --dry-run                       Print resolved sources and target paths without downloading.
  --help                          Show this help.

Examples:
  ./scripts/models/install_models.sh --dry-run
  ./scripts/models/install_models.sh --ensure-present --models-root=/mnt/d/agent-models
  ./scripts/models/install_models.sh --ensure-present --asset-set=all --huggingface-cache=/mnt/d/hf-cache
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help)
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

if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

if [ -f "$NATIVE_ENV_FILE" ]; then
  set -a
  source "$NATIVE_ENV_FILE"
  set +a
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

if [ "$DRY_RUN" = "1" ] || [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
  PY_ARGS+=("--dry-run")
fi

exec python "${PY_ARGS[@]}"
