# B3.45 Runtime Tail Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Довести хвост `B3.45` до завершённого состояния: синхронизировать weak-PC policy между preflight и runtime, зафиксировать env timeout overrides и прогнать целевые проверки.

**Architecture:** Основная bounded/hierarchical логика уже добавлена в `document_analysis` и `execution_runtime`. Остаток задачи состоит из устранения расхождения между `runtime_preflight` и `unified_model_server` для single-GPU `8GB` профиля и из фиксации timeout knobs в `backend/.env.hardware.override` для длительных heavy-stage вызовов.

**Tech Stack:** Python, FastAPI, Chainlit, pytest, llama.cpp/UMS.

---

### Task 1: Runtime weak-PC parity

**Files:**
- Modify: `backend/tests/test_unified_model_server_startup.py`
- Modify: `backend/services/model_manager/unified_model_server.py`

**Step 1: Write the failing test**

Добавить тест на `_build_model_placement_plan(...)` для single-GPU `8GB` профиля без env override:
- вход: embedding model (`qwen3-embedding-0.6b` или `labse-embedding`)
- GPU: один адаптер `8.0 GB`
- tier preference: `cuda`
- ожидание: runtime placement уходит в `cpu`

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_unified_model_server_startup.py -q -k weak_pc_runtime`
Expected: FAIL, потому что runtime пока пытается отдать embedding на GPU.

**Step 3: Write minimal implementation**

Добавить runtime-side helper для weak-PC embedding policy и использовать его в `_resolve_component_device_mode(...)` или `_build_model_placement_plan(...)` так, чтобы:
- default policy уводила embedders на CPU для single-GPU `<=8.5 GB`
- явный env override по-прежнему побеждал policy

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_unified_model_server_startup.py -q -k weak_pc_runtime`
Expected: PASS

### Task 2: Timeout env overrides

**Files:**
- Modify: `backend/.env.hardware.override`

**Step 1: Update env overrides**

Добавить:
- `UMS_INFER_TIMEOUT_S=600`
- `UMS_CLIENT_TIMEOUT_S=600`
- `UMS_RETRY_MAX_DELAY_S=30`

**Step 2: Sanity check**

Run: `sed -n '1,80p' backend/.env.hardware.override`
Expected: новые timeout knobs присутствуют и не ломают существующие CPU overrides.

### Task 3: Verification

**Files:**
- Test: `backend/tests/test_document_analysis.py`
- Test: `backend/tests/test_execution_runtime.py`
- Test: `backend/tests/test_runtime_preflight.py`
- Test: `backend/tests/test_unified_model_server_startup.py`

**Step 1: Run targeted verification**

Run:
- `pytest backend/tests/test_document_analysis.py -q`
- `pytest backend/tests/test_execution_runtime.py -q`
- `pytest backend/tests/test_runtime_preflight.py -q`
- `pytest backend/tests/test_unified_model_server_startup.py -q`

**Step 2: Record actual status**

Если какой-то suite падает, локализовать failure и исправить только связанный с `B3.45` хвост.
