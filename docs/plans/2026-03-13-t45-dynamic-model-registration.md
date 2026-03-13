# T4.5 Dynamic Model Registration

**Goal:** добавить backend-owned runtime registration/unregistration для локальных моделей в `UMS` без смешивания с `T4.6` port pool/scheduler.

## Context

После `T4.4` у `UMS` уже есть operator control API для:

- `GET /models`
- `GET /models/running`
- `POST /models/{model_id}/preload`
- `POST /models/{model_id}/activate`
- `POST /models/{model_id}/stop`

Но набор моделей всё ещё определяется только:

- `STATIC_MODELS_CONFIG`
- filesystem-discovery `.gguf` under `MODELS_DIR`

Этого недостаточно для operator-managed runtime registration:

- custom local GGUF outside canonical folder
- custom ST embedding directory
- explicit runtime alias / port / ctx / gpu_layers
- controlled unregister без удаления файла

## Scope

### In scope

- backend-owned dynamic registry в `UMS`
- persisted manifest для registered models
- explicit register/unregister endpoints
- `get_model_config()` сначала смотрит registered models, потом static/filesystem
- simple monotonic dynamic port allocation
- targeted tests + docs/TASKS sync

### Out of scope

- remote registries / model hub sync
- port pool reuse / scheduler / queue
- auth/ACL for ops endpoints
- hot migration / rebalance running processes
- raw model selector in Chainlit

## Minimal safe slice

### Registry model

Добавить runtime registry для dynamic models:

- `state["dynamic_models"]`
- persisted manifest file under backend runtime data

Recommended persisted path:

- `backend/.data/ums_dynamic_models.json`

Registry fields per model:

- `model_id`
- `type` (`gguf`, `gguf-vl`, `st`)
- `path`
- optional `mmproj`
- optional `ctx_size`
- optional `gpu_layers`
- `port`
- `registered_at`

### API shape

#### `POST /models/register`

Body:

```json
{
  "model_id": "qwen-custom-7b",
  "type": "gguf",
  "path": "/abs/path/to/model.gguf",
  "ctx_size": 8192,
  "gpu_layers": -1,
  "port": 8110
}
```

Rules:

- `model_id` must not collide with static config
- `model_id` must not collide with existing dynamic registration unless it is identical re-register
- `type` limited to `gguf | gguf-vl | st`
- `path` must exist
- `gguf` / `gguf-vl` use resolved local path
- `st` uses resolved local path
- `gguf-vl` requires `mmproj`
- if `port` omitted, assign next monotonic dynamic port

Response:

- `status=success`
- `action=register`
- `model` = standard model view

#### `DELETE /models/{model_id}/registration`

Rules:

- cannot unregister static models
- if process is running, stop it first through backend path
- remove registration from manifest and runtime state

Response:

- `status=success`
- `action=unregister`
- `model_id`

### Existing endpoints impact

- `GET /models` must include registered models
- `GET /models/running` unchanged except registered models appear naturally when running
- control endpoints from `T4.4` must work for registered models through same `_start_server` / `_stop_model` flow

## File plan

### Modify

- `backend/services/model_manager/unified_model_server.py`
- `backend/tests/test_unified_model_server_startup.py`
- `TASKS.md`
- `docs/plans/2026-03-13-t45-dynamic-model-registration.md`

### Optional

- `README.md` only if operator workflow needs one short note

## Execution order

1. Add runtime/persisted dynamic registry helpers
2. Add register/unregister endpoints
3. Update config resolution and model listing
4. Add focused tests for register/unregister/list/control path
5. Sync TASKS/docs
6. Run targeted verification
7. Run full backend unit suite

## Risks / guardrails

- Do not implement port reuse or scheduling here; only monotonic allocation
- Do not make Chainlit depend on registration API
- Do not break existing static/filesystem-discovery behavior
- If using persisted manifest, fail soft on corrupt file by logging warning + empty registry, and record follow-up in `TASKS.md`

## Verification

- `pytest backend/tests/test_unified_model_server_startup.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`

## Completed

- dynamic registry added in `UMS` runtime state: `state["dynamic_models"]`
- persisted manifest wired through `UMS_DYNAMIC_MODELS_REGISTRY_PATH`
  with default `backend/.data/ums_dynamic_models.json`
- `POST /models/register` implemented
- `DELETE /models/{model_id}/registration` implemented
- `get_model_config()` now resolves:
  - `STATIC_MODELS_CONFIG`
  - registered dynamic models
  - filesystem-discovered `.gguf`
- deterministic auto-port allocation added for both dynamic registrations and discovered filesystem models without introducing full port-pool logic
- unknown/corrupt registry file is handled soft with warning and empty runtime registry

### Verification Evidence

- `pytest backend/tests/test_unified_model_server_startup.py -vv` → `25 passed`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`
- phase-level full backend regression recorded in `TASKS.md`
