# T4.9 vLLM adapter в UMS

## Goal

Добавить в `UMS` backend-owned adapter для OpenAI-compatible `vLLM` server без full migration runtime stack.

## Safe scope

### In scope

- `BACKEND_MODE=vllm` как runtime switch для heavy text inference path
- upstream adapter к OpenAI-compatible `vLLM` server
- reuse текущего `UMS` control plane:
  - `/infer`
  - `/models`
  - `/models/running`
  - `/models/{model_id}/preload`
  - `/models/{model_id}/activate`
  - `/models/{model_id}/stop`
- remote placement metadata в model views / `/status`
- targeted tests + docs/TASKS sync

### Out of scope

- local process management для `vLLM` внутри `UMS`
- docker compose profile для `vLLM` runtime
- embeddings через `vLLM`
- `gguf-vl` / vision on `vLLM`
- full replacement `llama-server`

## Source constraints

По официальной документации `vLLM` предоставляет OpenAI-compatible HTTP server (`chat/completions`, `completions`, `embeddings`) через `vllm serve ...`.
Для safe slice используем только text-generation upstream и не меняем current embedding path.

## Planned slice

1. Add env/config surface:
   - `BACKEND_MODE=vllm`
   - `VLLM_BASE_URL`
   - `VLLM_API_KEY` (optional)
   - `VLLM_MODEL_ID_QWEN14B` (upstream served model name)
2. Add backend selection helpers in `UMS`:
   - detect whether model should use vLLM adapter
   - build upstream URL and auth headers
   - health/models probe for readiness
3. Add remote runtime sentinel:
   - lightweight pseudo-process for `state["processes"]`
   - placement metadata `placement_mode=remote-vllm`
4. Route `/infer` heavy text requests through vLLM upstream when enabled.
5. Keep local paths unchanged for:
   - embeddings (`st`)
   - `gguf-vl`
   - non-vLLM backend modes
6. Add tests:
   - remote preload/activate path
   - `/infer` proxy path for non-stream and stream
   - `/models/running` / `/status` metadata
   - unsupported remote path safety for embeddings/VL
7. Sync docs/TASKS.

## Verification

- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q -k "vllm or remote"`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py`
- `git diff --check`

## Implemented

- `UMS` умеет работать в `BACKEND_MODE=vllm` для heavy `gguf` text inference path без full runtime rewrite.
- Activation/preload path использует remote runtime sentinel вместо локального subprocess.
- `/infer` проксирует non-stream и stream запросы в OpenAI-compatible `vLLM` endpoints:
  - `/v1/completions`
  - `/v1/chat/completions`
- Upstream auth (`VLLM_API_KEY`) и served model id override (`VLLM_MODEL_ID_QWEN_14B_LLM`) поддержаны.
- `/status`, `/models`, `/models/running` публикуют `backend_mode=vllm` и `remote-vllm` placement metadata.
- Non-stream path больше не маскирует upstream HTTP errors как `success`.

## Rollout note

`T4.9` не включает самостоятельный deployment `vLLM` и не поднимает его через compose/profile. Это отдельная фаза `T4.10`.
