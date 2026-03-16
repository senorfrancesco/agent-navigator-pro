#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="$BACKEND_DIR/.env"
NATIVE_ENV_FILE="$BACKEND_DIR/.env.native"
RUNTIME_ENV_FILE="${AGENT_NAVIGATOR_RUNTIME_ENV_FILE:-$BACKEND_DIR/.env.runtime}"
TARGET="native"
PROFILE="${UMS_RUNTIME_PROFILE:-adaptive}"
NO_ATTACH=false
REPORT_ONLY=false
INSTALL=false
INSTALL_PLATFORM="auto"
ENSURE_MODELS=true
MODEL_ASSET_SET="${AGENT_NAVIGATOR_MODEL_ASSET_SET:-core}"
MODELS_ROOT="${AGENT_NAVIGATOR_MODELS_ROOT:-}"
HF_CACHE="${HF_HOME:-${AGENT_NAVIGATOR_HF_CACHE:-}}"

print_help() {
  cat <<EOF
launcher.sh

Canonical entrypoint for Agent Navigator runtime and guided install.

Usage:
  ./scripts/launcher.sh --target native --profile adaptive
  ./scripts/launcher.sh --target container --profile default
  ./scripts/launcher.sh --install --platform ubuntu
  ./scripts/launcher.sh --target native --models-root /mnt/d/agent-models

Flags:
  --target native|container
  --profile <runtime-profile>
  --platform auto|ubuntu|ubuntu-server|wsl|windows
  --asset-set core|all
  --models-root <path>
  --huggingface-cache <path>
  --skip-model-download
  --no-attach
  --report-only
  --install
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --help)
      print_help
      exit 0
      ;;
    --target)
      TARGET="$2"
      shift 2
      ;;
    --target=*)
      TARGET="${1#*=}"
      shift
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --profile=*)
      PROFILE="${1#*=}"
      shift
      ;;
    --no-attach)
      NO_ATTACH=true
      shift
      ;;
    --report-only)
      REPORT_ONLY=true
      shift
      ;;
    --install)
      INSTALL=true
      shift
      ;;
    --platform)
      INSTALL_PLATFORM="$2"
      shift 2
      ;;
    --platform=*)
      INSTALL_PLATFORM="${1#*=}"
      shift
      ;;
    --asset-set)
      MODEL_ASSET_SET="$2"
      shift 2
      ;;
    --asset-set=*)
      MODEL_ASSET_SET="${1#*=}"
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
    --skip-model-download)
      ENSURE_MODELS=false
      shift
      ;;
    *)
      echo "Неизвестный аргумент launcher.sh: $1" >&2
      exit 1
      ;;
  esac
done

if [ "$INSTALL" = true ]; then
  bash "$SCRIPT_DIR/bootstrap_env.sh" --install "--target=$TARGET" "--platform=$INSTALL_PLATFORM"
  exit $?
fi

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

bash "$SCRIPT_DIR/bootstrap_env.sh" --check "--target=$TARGET"

PREFLIGHT_ARGS=("apply" "--profile" "$PROFILE" "--output" "$RUNTIME_ENV_FILE")
if [ "$REPORT_ONLY" = true ]; then
  PREFLIGHT_ARGS+=("--report-only")
fi

PREFLIGHT_OUTPUT="$(python "$SCRIPT_DIR/runtime_preflight.py" "${PREFLIGHT_ARGS[@]}")"
echo "$PREFLIGHT_OUTPUT"

if [ -n "$MODELS_ROOT" ]; then
  MODELS_ROOT_ABS="$(python -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$MODELS_ROOT")"
  MODEL_PATH_LLM="$MODELS_ROOT_ABS/gguf/qwen-14b/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
  MODEL_PATH_VLM="$MODELS_ROOT_ABS/gguf/Qwen3-VL-8B-Q4/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
  MMPROJ_PATH="$MODELS_ROOT_ABS/gguf/Qwen3-VL-8B-Q4/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
  MODEL_PATH_EMBEDDING_INTENT="$MODELS_ROOT_ABS/st/Qwen3-Embedding-0.6B"
  MODEL_PATH_EMBEDDING_RETRIEVAL="$MODELS_ROOT_ABS/st/LaBSE"
  export MODEL_PATH_LLM MODEL_PATH_VLM MMPROJ_PATH MODEL_PATH_EMBEDDING_INTENT MODEL_PATH_EMBEDDING_RETRIEVAL
  cat >> "$RUNTIME_ENV_FILE" <<EOF
MODEL_PATH_LLM="$MODEL_PATH_LLM"
MODEL_PATH_VLM="$MODEL_PATH_VLM"
MMPROJ_PATH="$MMPROJ_PATH"
MODEL_PATH_EMBEDDING_INTENT="$MODEL_PATH_EMBEDDING_INTENT"
MODEL_PATH_EMBEDDING_RETRIEVAL="$MODEL_PATH_EMBEDDING_RETRIEVAL"
EOF
fi

if [ "$REPORT_ONLY" = true ]; then
  exit 0
fi

if [ "$ENSURE_MODELS" = true ]; then
  MODEL_ARGS=("--ensure-present" "--asset-set=$MODEL_ASSET_SET")
  if [ -n "$MODELS_ROOT" ]; then
    MODEL_ARGS+=("--models-root=$MODELS_ROOT")
  fi
  if [ -n "$HF_CACHE" ]; then
    MODEL_ARGS+=("--huggingface-cache=$HF_CACHE")
  fi
  if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
    MODEL_ARGS+=("--dry-run")
  fi
  bash "$SCRIPT_DIR/models/install_models.sh" "${MODEL_ARGS[@]}"
fi

if [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" = "1" ]; then
  echo "launcher:test-mode target=$TARGET profile=$PROFILE env_runtime=$RUNTIME_ENV_FILE no_attach=$NO_ATTACH"
  exit 0
fi

if [ "$TARGET" = "native" ]; then
  exec bash "$SCRIPT_DIR/run_native.sh" --from-launcher $([ "$NO_ATTACH" = true ] && echo "--no-attach")
elif [ "$TARGET" = "container" ]; then
  exec bash "$SCRIPT_DIR/run_all.sh" --from-launcher $([ "$NO_ATTACH" = true ] && echo "--no-attach")
else
  echo "Неподдерживаемый target: $TARGET" >&2
  exit 1
fi
