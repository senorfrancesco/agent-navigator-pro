#!/bin/bash

set -euo pipefail

MODEL_SOURCE="${VLLM_MODEL_SOURCE_QWEN_14B_LLM:-Qwen/Qwen2.5-14B-Instruct}"
SERVED_MODEL_ID="${VLLM_MODEL_ID_QWEN_14B_LLM:-qwen-14b-llm}"
INTERNAL_PORT="${VLLM_INTERNAL_PORT:-8000}"

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
