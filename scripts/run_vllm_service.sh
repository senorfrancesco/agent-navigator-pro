#!/bin/bash

set -euo pipefail

MODEL_SOURCE="${VLLM_MODEL_SOURCE_QWEN_14B_LLM:-Qwen/Qwen2.5-14B-Instruct}"
SERVED_MODEL_ID="${VLLM_MODEL_ID_QWEN_14B_LLM:-qwen-14b-llm}"
INTERNAL_PORT="${VLLM_INTERNAL_PORT:-8000}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<EOF
run_vllm_service.sh

Запускает `vllm serve` для Qwen runtime, используя параметры из переменных окружения.

Использование:
  ./scripts/run_vllm_service.sh

Флаги:
  У скрипта нет пользовательских CLI-флагов.
  -h, --help
      Показать эту справку.

Переменные окружения:
  VLLM_MODEL_SOURCE_QWEN_14B_LLM
      Источник модели для `vllm serve`.
  VLLM_MODEL_ID_QWEN_14B_LLM
      Идентификатор, под которым модель публикуется наружу.
  VLLM_INTERNAL_PORT
      Порт внутреннего vLLM сервиса.
  VLLM_API_KEY
      Если задан, включает API key защиту.
  VLLM_TENSOR_PARALLEL_SIZE
      Размер tensor parallelism.
  VLLM_GPU_MEMORY_UTILIZATION
      Доля GPU-памяти, разрешённая для vLLM.
  VLLM_MAX_MODEL_LEN
      Ограничение максимальной длины контекста.
EOF
  exit 0
fi

ARGS=(
  serve
  "$MODEL_SOURCE"
  --host
  "0.0.0.0"
  --port
  "$INTERNAL_PORT"
  --served-model-name
  "$SERVED_MODEL_ID"
)

if [ -n "${VLLM_API_KEY:-}" ]; then
  ARGS+=(--api-key "$VLLM_API_KEY")
fi

if [ -n "${VLLM_TENSOR_PARALLEL_SIZE:-}" ]; then
  ARGS+=(--tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE")
fi

if [ -n "${VLLM_GPU_MEMORY_UTILIZATION:-}" ]; then
  ARGS+=(--gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION")
fi

if [ -n "${VLLM_MAX_MODEL_LEN:-}" ]; then
  ARGS+=(--max-model-len "$VLLM_MAX_MODEL_LEN")
fi

echo "[vLLM] Starting served_model=$SERVED_MODEL_ID source=$MODEL_SOURCE port=$INTERNAL_PORT"
exec vllm "${ARGS[@]}"
