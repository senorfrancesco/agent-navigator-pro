## B3.16 — Проверить `llama-server` defunct / uptime после простоя

**Status:** Implemented on 2026-03-13

### Goal

Убрать stale/dead local model processes из `UMS` runtime state, чтобы после простоя `/status`, `/models` и subsequent start/activate paths не считали умерший `llama-server` живым.

### Current problem

Сейчас `UMS`:

- хранит локальные процессы в `state["processes"]`;
- очищает их при `_stop_model(...)`;
- partially cleans stale `existing_proc` только внутри `_start_server(...)`.

Но если локальный `llama-server` умер сам после idle/ошибки:

- `get_status()` может продолжать показывать модель как running;
- `list_running_models()` и `_build_model_view()` не гарантируют pruning stale process state;
- `active_model` может указывать на уже мёртвый process.

### Scope

В этой фазе:

1. Добавить runtime helper для dead-process sweep.
2. Применять его в status/model view surface.
3. Добавить tests на stale process cleanup.

### Non-goals

- watchdog/auto-restart loop;
- keepalive;
- idle unload policy;
- launcher/tmux cleanup logic.

### Implementation steps

#### Step 1. Runtime helper

В `backend/services/model_manager/unified_model_server.py` добавить helper, например:

- `_is_managed_process_alive(proc)`
- `_prune_dead_processes()`

Правила:

- `_RemoteProcess` не считать dead local process;
- если локальный process имеет `poll() is not None`, удалять:
  - `state["processes"][model_id]`
  - `state["placements"][model_id]`
  - `state["active_model"]`, если указывает на этот model id
- не выполнять destructive external kill в этом helper.

#### Step 2. Wiring

Вызывать pruning перед:

- `_build_model_view(...)`
- `GET /models/running`
- `GET /status`

Опционально:

- перед `GET /models`

### Tests

Добавить в `backend/tests/test_unified_model_server_startup.py`:

1. dead local process is pruned from `/status`
2. dead local process is omitted from `/models/running`
3. dead active model clears `active_heavy_model`
4. `_RemoteProcess` is not pruned by local dead-process sweep

### Verification

Targeted:

```bash
pytest backend/tests/test_unified_model_server_startup.py -q -k "dead_process or running_models or status_exposes"
```

Phase regression:

```bash
cd backend && pytest tests/ -q -m "not integration"
```

Static:

```bash
python -m py_compile backend/services/model_manager/unified_model_server.py \
  backend/tests/test_unified_model_server_startup.py
git diff --check
```

### Verification status

Passed:

- `pytest backend/tests/test_unified_model_server_startup.py -q -k "dead_local_processes or status_prunes_dead_local_process_and_clears_active_model or status_keeps_remote_process_registered or models_running or status_exposes_current_placements"` -> `5 passed`
- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q` -> runtime assertions pass; command then reproduces the known old pytest tail-hang after test completion
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`

### Done when

- dead local `llama-server` process больше не висит в runtime state после idle/death;
- `/status` и `/models/running` честно отражают только живые процессы;
- `active_heavy_model` не указывает на dead process;
- targeted tests зелёные;
- статус зафиксирован в `TASKS.md`.
