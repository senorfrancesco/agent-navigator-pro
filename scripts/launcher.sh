#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
ENV_FILE="${AGENT_NAVIGATOR_BACKEND_ENV_FILE:-$BACKEND_DIR/.env}"
RUNTIME_ENV_FILE="${AGENT_NAVIGATOR_RUNTIME_ENV_FILE:-$BACKEND_DIR/.env.runtime}"
TARGET="native"
PROFILE="${UMS_RUNTIME_PROFILE:-adaptive}"
NO_ATTACH=false
REPORT_ONLY=false
NON_INTERACTIVE=false
REVIEW_RUNTIME=false
INSTALL=false
INSTALL_PLATFORM="auto"
ENSURE_MODELS=false
MODEL_ASSET_SET="${AGENT_NAVIGATOR_MODEL_ASSET_SET:-core}"
MODELS_ROOT="${AGENT_NAVIGATOR_MODELS_ROOT:-}"
HF_CACHE="${HF_HOME:-${AGENT_NAVIGATOR_HF_CACHE:-}}"
GPU_LAYERS_MODE_OVERRIDE="${GPU_LAYERS_MODE:-}"
GPU_LAYERS_OVERRIDE_VALUE="${N_GPU_LAYERS_OVERRIDE:-}"
DEVICE_MODE_OVERRIDE="${DEVICE_MODE:-}"
LLM_DEVICE_MODE_OVERRIDE="${LLM_DEVICE_MODE:-}"
VLM_DEVICE_MODE_OVERRIDE="${VLM_DEVICE_MODE:-}"
INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE="${INTENT_EMBEDDER_DEVICE_MODE:-}"
RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE="${RETRIEVAL_EMBEDDER_DEVICE_MODE:-}"

print_help() {
  cat <<EOF
launcher.sh

Compatibility wrapper for manual/native shell usage around the Python-first operator control plane.
Operator UI and /operator/* endpoints remain the canonical product API; this script is kept for CLI, install and recovery workflows.

Использование:
  ./scripts/launcher.sh --target native --profile adaptive
  ./scripts/launcher.sh --target container --profile default
  ./scripts/launcher.sh --install --platform ubuntu
  ./scripts/launcher.sh --target native --models-root /mnt/d/agent-models

Флаги:
  --target native|container
      Целевой runtime path: host-only native запуск или compose/container запуск.
  --profile <runtime-profile>
      Runtime-профиль preflight слоя: default, adaptive или manual.
  --platform auto|ubuntu|ubuntu-server|wsl|windows
      Платформа guided install path; используется вместе с --install.
  --asset-set core|all
      Набор моделей для pre-launch ensure-present шага.
  --models-root <path>
      Корневой каталог моделей вместо стандартного layout внутри backend/models.
  --huggingface-cache <path>
      Каталог кэша Hugging Face для model provisioning.
  --gpu-layers-mode auto|max|manual
      Режим выбора GPU layers для LLM.
  --gpu-layers <int>
      Явное число GPU layers для manual режима.
  --device-mode cpu|gpu|hybrid
      Общий device-mode для runtime planning.
  --llm-device-mode cpu|gpu|hybrid
      Переопределение device-mode только для LLM.
  --vlm-device-mode cpu|gpu|hybrid
      Переопределение device-mode только для VLM.
  --intent-embedder-device-mode cpu|gpu|hybrid
      Переопределение device-mode для intent embedder.
  --retrieval-embedder-device-mode cpu|gpu|hybrid
      Переопределение device-mode для retrieval embedder.
  --non-interactive
      Не задавать интерактивных вопросов при review/runtime guidance.
  --review-runtime
      Показать и подтвердить runtime plan перед запуском.
  --skip-model-download
      Compatibility alias: не запускать model provisioning phase.
  --ensure-model-download
      Явно включить model provisioning phase перед запуском.
  --no-attach
      Не подключаться к tmux после запуска target runner.
  --report-only
      Только вывести runtime plan без записи .env.runtime и без запуска.
  --install
      Запустить guided install path вместо runtime orchestration.
  -h, --help
      Показать эту справку.

Interactive review:
  1. launcher показывает runtime plan только для текущего запуска
  2. launcher записывает applied backend/.env.runtime
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
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
    --gpu-layers-mode)
      GPU_LAYERS_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --gpu-layers-mode=*)
      GPU_LAYERS_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --gpu-layers)
      GPU_LAYERS_OVERRIDE_VALUE="$2"
      shift 2
      ;;
    --gpu-layers=*)
      GPU_LAYERS_OVERRIDE_VALUE="${1#*=}"
      shift
      ;;
    --device-mode)
      DEVICE_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --device-mode=*)
      DEVICE_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --llm-device-mode)
      LLM_DEVICE_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --llm-device-mode=*)
      LLM_DEVICE_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --vlm-device-mode)
      VLM_DEVICE_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --vlm-device-mode=*)
      VLM_DEVICE_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --intent-embedder-device-mode)
      INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --intent-embedder-device-mode=*)
      INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --retrieval-embedder-device-mode)
      RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE="$2"
      shift 2
      ;;
    --retrieval-embedder-device-mode=*)
      RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE="${1#*=}"
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=true
      shift
      ;;
    --review-runtime)
      REVIEW_RUNTIME=true
      shift
      ;;
    --skip-model-download)
      ENSURE_MODELS=false
      shift
      ;;
    --ensure-model-download)
      ENSURE_MODELS=true
      shift
      ;;
    *)
      echo "Неизвестный аргумент launcher.sh: $1" >&2
      exit 1
      ;;
  esac
done

source_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  set -a
  source "$file"
  set +a
}

build_preflight_args() {
  local command="$1"
  local output_path="${2:-}"
  local -a args=("$command" "--profile" "$PROFILE")
  if [ -n "$DEVICE_MODE_OVERRIDE" ]; then
    args+=("--device-mode" "$DEVICE_MODE_OVERRIDE")
  fi
  if [ -n "$LLM_DEVICE_MODE_OVERRIDE" ]; then
    args+=("--llm-device-mode" "$LLM_DEVICE_MODE_OVERRIDE")
  fi
  if [ -n "$VLM_DEVICE_MODE_OVERRIDE" ]; then
    args+=("--vlm-device-mode" "$VLM_DEVICE_MODE_OVERRIDE")
  fi
  if [ -n "$INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE" ]; then
    args+=("--intent-embedder-device-mode" "$INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE")
  fi
  if [ -n "$RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE" ]; then
    args+=("--retrieval-embedder-device-mode" "$RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE")
  fi
  if [ -n "$GPU_LAYERS_MODE_OVERRIDE" ]; then
    args+=("--gpu-layers-mode" "$GPU_LAYERS_MODE_OVERRIDE")
  fi
  if [ -n "$GPU_LAYERS_OVERRIDE_VALUE" ]; then
    args+=("--gpu-layers" "$GPU_LAYERS_OVERRIDE_VALUE")
  fi
  if [ "$command" = "apply" ] && [ -n "$output_path" ]; then
    args+=("--output" "$output_path")
  fi
  if [ "$REPORT_ONLY" = true ] && [ "$command" = "apply" ]; then
    args+=("--report-only")
  fi
  printf '%s\n' "${args[@]}"
}

run_preflight_json() {
  local command="$1"
  local output_path="${2:-}"
  local -a cmd=("python" "$SCRIPT_DIR/runtime_preflight.py")
  while IFS= read -r line; do
    [ -n "$line" ] && cmd+=("$line")
  done < <(build_preflight_args "$command" "$output_path")
  "${cmd[@]}"
}

render_runtime_review_summary() {
  local plan_json="$1"
  python - "$plan_json" <<'PY'
import json, sys
plan = json.loads(sys.argv[1])
hw = plan.get("hardware") or {}
best_gpu = hw.get("best_gpu") or {}
gpus = hw.get("gpus") or []
warnings = plan.get("warnings") or []
placements = plan.get("placements") or {}
admission = plan.get("admission") or {}
print("Runtime plan:")
print(f"  profile: {plan.get('runtime_profile')}")
print(f"  device_mode: {plan.get('device_mode')}")
print(f"  llm: {plan.get('llm_model_id')} {plan.get('llm_quant')}")
print(f"  ctx_size: {plan.get('llm_ctx_size')}")
print(f"  gpu_layers: {plan.get('llm_gpu_layers')} (mode={plan.get('gpu_layers_mode')}, source={plan.get('gpu_layers_source')})")
print(f"  embedding: {plan.get('embedding_backend')} / {plan.get('embedding_device')}")
print(f"  tier: {plan.get('tier')} rag_mode={plan.get('rag_mode')} ({plan.get('rag_mode_label')})")
print(f"  effective_context_tokens: {plan.get('effective_context_tokens')}")
print(f"  retrieved_context_tokens_budget: {plan.get('retrieved_context_tokens_budget')}")
print(f"  detected_gpu_count: {hw.get('gpu_count')}")
if best_gpu:
    print(f"  best_gpu: {best_gpu.get('name')} total={best_gpu.get('total_vram_gb')}GB free={best_gpu.get('free_vram_gb')}GB")
if gpus:
    print("  gpu_inventory:")
    for gpu in gpus:
        print(
            f"    - gpu[{gpu.get('index')}]: {gpu.get('name')} "
            f"total={gpu.get('total_vram_gb')}GB free={gpu.get('free_vram_gb')}GB"
        )
if placements:
    print("  placements:")
    for key in ("llm", "vlm", "intent_embedder", "retrieval_embedder"):
        placement = placements.get(key) or {}
        if not placement:
            continue
        summary = ", ".join(f"{k}={v}" for k, v in placement.items())
        print(f"    - {key}: {summary}")
if admission:
    print("  admission:")
    for key, item in admission.items():
        if not item:
            continue
        summary = ", ".join(
            f"{field}={item.get(field)}"
            for field in ("component", "requested_device", "resolved_device", "admission", "effective_token_budget")
            if item.get(field) is not None
        )
        print(f"    - {key}: {summary}")
for warning in warnings:
    print(f"  warning: {warning}")
PY
}

render_applied_env_preview() {
  local plan_json="$1"
  python - "$plan_json" <<'PY'
import json, sys
plan = json.loads(sys.argv[1])
component_modes = plan.get("component_device_modes") or {}
component_sources = plan.get("component_device_mode_sources") or {}
items = [
    ("DEVICE_MODE", plan.get("device_mode"), plan.get("decision_source", "auto")),
    ("GPU_LAYERS_MODE", plan.get("gpu_layers_mode"), plan.get("gpu_layers_mode_source", "auto")),
    ("N_GPU_LAYERS_OVERRIDE", plan.get("llm_gpu_layers"), plan.get("gpu_layers_source", "tier_auto")),
    ("LLM_DEVICE_MODE", component_modes.get("llm"), component_sources.get("llm", "auto")),
    ("VLM_DEVICE_MODE", component_modes.get("vlm"), component_sources.get("vlm", "auto")),
    ("INTENT_EMBEDDER_DEVICE_MODE", component_modes.get("intent_embedder"), component_sources.get("intent_embedder", "auto")),
    ("RETRIEVAL_EMBEDDER_DEVICE_MODE", component_modes.get("retrieval_embedder"), component_sources.get("retrieval_embedder", "auto")),
]
print("Applied env for this run (.env.runtime):")
for key, value, source in items:
    print(f"  {key}={value} (source={source})")
PY
}

if [ "$INSTALL" = true ]; then
  bash "$SCRIPT_DIR/bootstrap_env.sh" --install "--target=$TARGET" "--platform=$INSTALL_PLATFORM"
  exit $?
fi

source_env_file "$ENV_FILE"
bash "$SCRIPT_DIR/bootstrap_env.sh" --check "--target=$TARGET"

if [ -z "$GPU_LAYERS_MODE_OVERRIDE" ]; then
  GPU_LAYERS_MODE_OVERRIDE="${GPU_LAYERS_MODE:-}"
fi
if [ -z "$GPU_LAYERS_OVERRIDE_VALUE" ]; then
  GPU_LAYERS_OVERRIDE_VALUE="${N_GPU_LAYERS_OVERRIDE:-}"
fi
if [ -z "$DEVICE_MODE_OVERRIDE" ]; then
  DEVICE_MODE_OVERRIDE="${DEVICE_MODE:-}"
fi
if [ -z "$LLM_DEVICE_MODE_OVERRIDE" ]; then
  LLM_DEVICE_MODE_OVERRIDE="${LLM_DEVICE_MODE:-}"
fi
if [ -z "$VLM_DEVICE_MODE_OVERRIDE" ]; then
  VLM_DEVICE_MODE_OVERRIDE="${VLM_DEVICE_MODE:-}"
fi
if [ -z "$INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE" ]; then
  INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE="${INTENT_EMBEDDER_DEVICE_MODE:-}"
fi
if [ -z "$RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE" ]; then
  RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE="${RETRIEVAL_EMBEDDER_DEVICE_MODE:-}"
fi

INTERACTIVE_REVIEW=false
if [ "$TARGET" = "native" ] && [ "$REPORT_ONLY" = false ]; then
  if [ "$REVIEW_RUNTIME" = true ]; then
    INTERACTIVE_REVIEW=true
  elif [ "${AGENT_NAVIGATOR_TEST_MODE:-0}" != "1" ] && [ "$NON_INTERACTIVE" = false ] && [ -t 0 ] && [ -t 1 ]; then
    INTERACTIVE_REVIEW=true
  fi
fi

if [ "$INTERACTIVE_REVIEW" = true ]; then
  PLAN_PREVIEW="$(run_preflight_json "plan")"
  echo "$PLAN_PREVIEW"
  render_runtime_review_summary "$PLAN_PREVIEW"
  echo ""
  render_applied_env_preview "$PLAN_PREVIEW"
  echo ""
  read -r -p "Настроить план для текущего запуска? [Y/m=max/u=manual/d=device/c=components/q=cancel]: " REVIEW_CHOICE
  case "$(printf '%s' "$REVIEW_CHOICE" | tr '[:upper:]' '[:lower:]')" in
    ""|"y"|"yes")
      ;;
    "m"|"max")
      GPU_LAYERS_MODE_OVERRIDE="max"
      GPU_LAYERS_OVERRIDE_VALUE=""
      ;;
    "u"|"manual")
      GPU_LAYERS_MODE_OVERRIDE="manual"
      read -r -p "Введите количество GPU layers: " GPU_LAYERS_OVERRIDE_VALUE
      ;;
    "d"|"device")
      read -r -p "device_mode [cpu/gpu/hybrid]: " DEVICE_MODE_OVERRIDE
      ;;
    "c"|"components")
      read -r -p "llm_device_mode [cpu/gpu/hybrid, Enter=skip]: " LLM_DEVICE_MODE_OVERRIDE
      read -r -p "vlm_device_mode [cpu/gpu/hybrid, Enter=skip]: " VLM_DEVICE_MODE_OVERRIDE
      read -r -p "intent_embedder_device_mode [cpu/gpu/hybrid, Enter=skip]: " INTENT_EMBEDDER_DEVICE_MODE_OVERRIDE
      read -r -p "retrieval_embedder_device_mode [cpu/gpu/hybrid, Enter=skip]: " RETRIEVAL_EMBEDDER_DEVICE_MODE_OVERRIDE
      ;;
    "q"|"quit")
      echo "launcher:cancelled"
      exit 1
      ;;
  esac
  PLAN_PREVIEW="$(run_preflight_json "plan")"
  echo ""
  render_runtime_review_summary "$PLAN_PREVIEW"
  echo ""
  render_applied_env_preview "$PLAN_PREVIEW"
fi

PREFLIGHT_OUTPUT="$(run_preflight_json "apply" "$RUNTIME_ENV_FILE")"
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
