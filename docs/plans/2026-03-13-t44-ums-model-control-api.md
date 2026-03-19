# T4.4 UMS Model Control API

**Goal:** добавить backend-owned control API для `UMS`, чтобы runtime/operators могли явно управлять жизненным циклом моделей без ad hoc restarts и без ручного ковыряния процесса через launcher.

## Context

Сейчас `UMS` уже умеет:

- `_start_server(model_id, device_mode)`
- `_stop_model(model_id)`
- `get_model_config(model_id)`
- `/infer`
- `/v1/embeddings`
- `/status`

Но это внутренний control surface. Публичного ops API для:

- preload
- stop
- list available models
- list running models
- switch active heavy model

сейчас нет.

После `B3.22`, `B3.23`, `T4.13`, `T4.14` это следующий естественный блок:
- runtime metadata уже backend-owned
- placement уже backend-owned
- launcher/preflight уже есть
- теперь нужен явный control API поверх существующей runtime логики

## Scope

### In scope

- explicit UMS endpoints for model control
- operator-safe JSON contract
- list available/running models
- preload/start model by id
- stop model by id
- switch active heavy model through same backend path
- tests for status/control surface
- docs/TASKS sync

### Out of scope

- UI selector для raw model control
- dynamic registration from remote registries
- scheduler / port pool / concurrency policy
- auth/ACL hardening for ops surface

## Proposed endpoints

### `GET /models`

Returns:

- statically configured models
- filesystem-discovered GGUF models
- model type
- port
- resolved path
- running flag
- active flag

### `POST /models/{model_id}/preload`

Request body:

```json
{
  "device_mode": "gpu"
}
```

Behavior:

- validates model exists
- starts server if not running
- returns placement/runtime metadata

### `POST /models/{model_id}/activate`

For heavy LLM/VLM models:

- stops conflicting heavy model if needed
- starts target model
- marks it active in runtime state

### `POST /models/{model_id}/stop`

Behavior:

- no-op success if already stopped
- removes process/placement metadata

### `GET /models/running`

Compact operator view:

- running model ids
- `active_heavy_model`
- `placements`

## Contract notes

- API stays backend/ops-oriented; no raw GPU placement selector in Chainlit
- `device_mode` stays optional and validated against `DeviceMode`
- responses should include:
  - `model_id`
  - `running`
  - `active`
  - `placement`
  - `port`

## File plan

### Modify

- `backend/services/model_manager/unified_model_server.py`
- `backend/tests/test_unified_model_server_startup.py`
- `backend/tests/test_unified_model_server_streaming.py` only if shared state contract changes
- `TASKS.md`
- `README.md` only if operator usage needs a short note

## Execution order

1. Extract helpers for available/running model introspection
2. Add explicit control endpoints
3. Add focused tests
4. Sync TASKS/docs
5. Run targeted verification
6. Run full backend unit suite

## Verification

- `pytest backend/tests/test_unified_model_server_startup.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`

## Completed

- `GET /models` реализован с operator-friendly model view:
  - `model_id`
  - `type`
  - `port`
  - `running`
  - `active`
  - `path`
  - `resolved_path`
  - `placement`
- `GET /models/running` реализован как compact operator endpoint:
  - `active_heavy_model`
  - `running_model_ids`
  - `running_models`
  - `placements`
- `POST /models/{model_id}/preload`, `activate`, `stop` реализованы поверх существующих `_start_server` / `_stop_model`
- unknown model ids получают `404 Model <id> not found`
- targeted API tests добавлены в `backend/tests/test_unified_model_server_startup.py`

### Verification Evidence

- `pytest backend/tests/test_unified_model_server_startup.py -q` → `20 passed`
- `cd backend && pytest tests/ -q -m "not integration"` → `424 passed, 4 deselected`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`
