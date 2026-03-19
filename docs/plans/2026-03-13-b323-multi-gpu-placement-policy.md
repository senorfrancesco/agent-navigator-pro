# B3.23 Multi-GPU Placement Policy for LLM and Embeddings

**Goal:** ввести backend-owned placement policy для `UMS`, чтобы LLM и embedding servers запускались с прозрачным и тестируемым GPU placement plan, а не на implicit `cuda|cpu` и равномерном `tensor-split` по всем GPU.

**Status:** completed

## Safe slice

На этой фазе не делаем scheduler и не делаем hot migration процессов. Нужен только:

- deterministic placement plan
- env overrides
- status/report surface
- runtime apply path inside `UMS`

## In scope

- placement helpers inside `backend/services/model_manager/unified_model_server.py`
- weighted `tensor-split` for multi-GPU GGUF startup
- explicit embedding GPU pinning (`cuda:<idx>`) for ST server
- `/status` metadata for current placement
- tests for placement plan and startup command generation
- docs/TASKS sync

## Out of scope

- per-request migration of already running models
- multi-process scheduler / queue
- UI raw placement selector
- fine-grained GPU reservations across multiple concurrent heavy models

## Policy

### LLM
- if `device_mode=cpu` or no GPUs -> CPU placement
- else:
  - use `UMS_LLM_GPU_INDICES` if set and valid
  - otherwise select from visible GPUs by free VRAM
  - reject GPUs below `UMS_LLM_MIN_FREE_VRAM_GB`
  - reject heavily imbalanced GPUs below `UMS_LLM_MIN_BALANCE_RATIO`
  - if no valid multi-GPU set remains, fall back to the best single GPU
  - if more than one GPU selected:
    - compute weighted `tensor-split` from free VRAM
    - expose selected GPU indices and split ratios in status

### Embeddings
- if `device_mode=cpu` or no GPUs -> CPU
- else:
  - respect tier-level `embedding_device=cpu` as an explicit CPU preference
  - use `UMS_EMBEDDING_GPU_INDEX` if set and valid
  - otherwise prefer the best GPU that is not already occupied by an active heavy LLM placement
  - if all GPUs are occupied by active heavy placement, fall back to CPU instead of silently competing on `cuda:0`
  - start ST server with explicit `cuda:<idx>`

## Status surface

`UMS /status` publishes backend-owned placement metadata for operators:

- `placements[model_id].placement_mode`
- `placements[model_id].gpu_indices`
- `placements[model_id].tensor_split`
- `placements[model_id].device_arg`

This metadata is operational. It is intentionally not promoted into `Chainlit` UI selectors or user-facing effective settings.

## Follow-up boundaries

Still out of scope for this phase:

- per-request hot migration of already running processes
- global multi-process scheduler / queue
- fine-grained GPU reservations across several heavy models
- UI-level raw placement controls

These remain separate follow-ups after `B3.23`.

## Verification

- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_runtime_preflight.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`

## Definition of Done

- startup command generation uses explicit placement policy
- `/status` exposes placement metadata for operators
- LLM multi-GPU split is no longer hardcoded as equal weights
- embedding placement is explicit and test-covered

## Implemented

- `UMS` now keeps backend-owned `placements` metadata in runtime state and exposes it via `/status`.
- GGUF startup uses deterministic GPU admission:
  - optional allowlist via `UMS_LLM_GPU_INDICES`
  - free-VRAM floor via `UMS_LLM_MIN_FREE_VRAM_GB`
  - imbalance filtering via `UMS_LLM_MIN_BALANCE_RATIO`
  - weighted `--tensor-split` from actual free VRAM instead of equal shares
- SentenceTransformer startup now uses explicit placement:
  - `cuda:<idx>` instead of implicit `cuda`
  - `UMS_EMBEDDING_GPU_INDEX` override
  - tier preference `embedding_device=cpu`
  - preference for a non-LLM GPU when an active heavy model is already pinned
  - CPU fallback if all visible GPUs are occupied by the heavy placement

## Verification evidence

- `pytest backend/tests/test_unified_model_server_startup.py -q`
- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_runtime_preflight.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`
