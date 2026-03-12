# B3.22 Dynamic Selection of Models and Embedders

**Goal:** убрать размазанные по `chainlit_app.py` / `agent_api.py` константы для LLM и embedder model ids и свести их к одному backend-owned resolver слою, не вводя raw model-id selector в UI.

## Scope

### In scope
- backend-resolved contract для:
  - `resolved_model_id`
  - `resolved_intent_embedder_model_id`
  - `resolved_retrieval_embedder_model_id`
- env-backed mapping для canonical profiles
- использование resolved embedder ids в:
  - Chainlit classifier pre-init
  - Chainlit retrieval adapter
  - API execution adapter
- tests и docs sync

### Out of scope
- per-request hot-switching уже загруженных UMS models
- новый UI selector с raw model ids
- automatic migration retrieval baseline away from `LaBSE`

## Safe first slice

1. В `ui_control_plane.py`:
   - добавить canonical embedder profiles и env-backed resolver
   - по умолчанию сохранить текущий verdict:
     - intent embedder -> `qwen3-embedding-0.6b`
     - retrieval/legal embedder -> `labse-embedding`
   - вернуть в `effective_settings`:
     - `intent_embedder_profile`
     - `retrieval_embedder_profile`
     - `resolved_intent_embedder_model_id`
     - `resolved_retrieval_embedder_model_id`

2. В runtime adapters:
   - `chainlit_app.py` больше не должен брать embedder ids из top-level constants
   - `agent_api.py` retrieval adapter должен брать retrieval embedder из `effective_settings`

3. В tests:
   - `test_ui_control_plane.py`
   - `test_chainlit_runtime_mode.py`
   - `test_agent_api_orchestrate.py`

## Verification

- `pytest backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/orchestrator/ui_control_plane.py backend/orchestrator/chainlit_app.py backend/orchestrator/agent_api.py`
- `git diff --check`

## Definition of Done

- resolved embedder ids централизованы в backend control-plane contract
- `chainlit_app.py` и `agent_api.py` перестают использовать hardcoded retrieval/intent embedder ids как source of truth
- defaults не меняют текущий production verdict (`Qwen3` for intent, `LaBSE` for retrieval)
- phase-level verification зелёная

## Status

Completed on 2026-03-13.

### Delivered
- `ui_control_plane.py` now resolves:
  - `resolved_model_id`
  - `resolved_intent_embedder_model_id`
  - `resolved_retrieval_embedder_model_id`
- canonical env-backed embedder mapping added:
  - `CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL`
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL`
  - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL`
- `chainlit_app.py` classifier pre-init now uses the resolved intent embedder
- `chainlit_app.py` and `agent_api.py` retrieval adapters now use the resolved retrieval embedder
- effective config summaries expose resolved embedder ids alongside `resolved_model_id`

### Verification
- `pytest backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
- `cd backend && pytest tests/ -q -m "not integration"` -> `397 passed, 4 deselected`

### Deferred follow-up
- per-request hot-switching of already loaded UMS models/embedders remains a separate runtime API concern
