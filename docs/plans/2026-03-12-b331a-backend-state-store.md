# B3.31a Backend State Store Implementation Plan

Status: implemented on 2026-03-12.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Перенести execution-critical orchestration state из UI-owned `Chainlit` session storage в backend-owned state store без ломки текущего execution contract.

**Outcome:** Goal achieved for the current architecture slice.
- Added `backend/orchestrator/state_store.py` with `orchestrator_runs` backend persistence.
- Unified execution path now returns backend-owned `run_id/state_ref/state_version` and persists `pending_action_id`, `resume_state_blob`, `checkpoint_blob`.
- `Chainlit` resume restores backend snapshot first and falls back to legacy thread/history bootstrap only when store data is absent.
- Full backend non-integration suite passed after integration.

**Pragmatic compromise kept intentionally:**
- store backend currently uses SQLite fallback via `ORCHESTRATOR_STATE_DB_URL`; Postgres-backed production implementation remains a follow-up, not a blocker;
- `resume_state_blob` still stores `documents_by_id` snapshot for practical document-workflow resume, until a stricter document-reference layer is introduced.

**Architecture:** Ввести backend repository/store для `orchestrator_runs`, дать ему dev-safe SQLite fallback и интегрировать его в unified execution path. `execution_runtime` продолжает быть каноническим core, а `Chainlit` и API только подают `thread_id/session_id` и получают `run_id/state_ref/pending_action_id` из backend store. Resume в `Chainlit` сначала восстанавливает backend snapshot, и только потом делает legacy UI bootstrap.

**Tech Stack:** Python 3.11, sqlite3, FastAPI, Chainlit, pytest

---

### Task 1: Добавить backend state store abstraction

**Files:**
- Create: `backend/orchestrator/state_store.py`
- Test: `backend/tests/test_state_store.py`

**Step 1: Write the failing tests**

Покрыть:
- создание run по `thread_id/session_id`
- повторное получение того же run для того же thread/session locator
- сохранение `pending_action_id`, `resume_state_blob`, `checkpoint_blob`
- optimistic version increment on update

**Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_state_store.py -q`

**Step 3: Write minimal implementation**

Добавить:
- `OrchestrationRunRecord`
- `BackendStateSnapshot`
- `OrchestrationStateStore` protocol
- `SQLiteOrchestrationStateStore`
- `get_orchestration_state_store()`

Schema:
- `orchestrator_runs`
  - `run_id`
  - `thread_id`
  - `session_id`
  - `workflow_type`
  - `status`
  - `state_ref`
  - `pending_action_id`
  - `resume_state_blob`
  - `checkpoint_blob`
  - `version`
  - `created_at`
  - `updated_at`
  - `last_error`
  - `idempotency_key`

SQLite fallback по env:
- `ORCHESTRATOR_STATE_DB_URL`
- default: `sqlite:///.data/orchestrator_state.db`

**Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_state_store.py -q`

### Task 2: Интегрировать store в execution/API path

**Files:**
- Modify: `backend/orchestrator/execution_runtime.py`
- Modify: `backend/orchestrator/agent_api.py`
- Modify: `backend/tests/test_execution_runtime.py`
- Modify: `backend/tests/test_agent_api_orchestrate.py`

**Step 1: Write the failing tests**

Покрыть:
- `execute_orchestration()` получает backend-owned `run_id/state_ref`
- `pending_action_id` сохраняется в store
- API `/execute_orchestration` возвращает `state_ref` вида `run:<uuid>`
- повторный запрос с тем же `thread_id/session_id` использует тот же `run_id`

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_execution_runtime.py -q`
- `pytest backend/tests/test_agent_api_orchestrate.py -q`

**Step 3: Write minimal implementation**

Добавить helper flow:
- `prepare_orchestration_run(...)`
- `persist_execution_result(...)`

Интеграция:
- `agent_api.py` подготавливает run-context до вызова execution
- `execution_runtime.py` уважает уже переданный `run_id/state_ref`
- после выполнения backend store сохраняет:
  - `status`
  - `pending_action_id`
  - `resume_state_blob`
  - `checkpoint_blob`
  - `last_error`

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q`

### Task 3: Интегрировать backend snapshot в Chainlit resume/session sync

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`
- Modify: `backend/tests/test_chainlit_runtime_mode.py`
- Modify: `backend/tests/test_chainlit_resume_state.py` (create if absent)

**Step 1: Write the failing tests**

Покрыть:
- `on_message` после backend response синхронизирует backend state snapshot
- `on_chat_resume` сначала пытается восстановить backend snapshot
- `pending_action`, `control_plane_state`, `effective_settings`, `active_doc_ids` восстанавливаются из backend store

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_chainlit_runtime_mode.py -q`
- `pytest backend/tests/test_chainlit_resume_state.py -q`

**Step 3: Write minimal implementation**

Добавить в `chainlit_app.py`:
- helper для построения backend snapshot из user_session
- helper для применения backend snapshot в user_session
- sync после `_render_execution_response()`
- restore при `on_chat_resume`

Пока допустим pragmatic snapshot:
- `pending_action`
- `control_plane_state`
- `effective_settings`
- `active_doc_ids`
- `active_mode`
- `last_route`
- `last_executor`
- `last_trace_id`
- `documents_by_id`
- `documents_by_name`

Если в snapshot нет данных, оставить текущий legacy bootstrap.

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_chainlit_runtime_mode.py backend/tests/test_chainlit_resume_state.py -q`

### Task 4: Закрыть B3.31a verification и docs sync

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-11-orchestration-stabilization-plan.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`

**Step 1: Run phase-level regression**

Run:
- `pytest backend/tests/test_state_store.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_chainlit_resume_state.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/orchestrator/state_store.py backend/orchestrator/agent_api.py backend/orchestrator/execution_runtime.py backend/orchestrator/chainlit_app.py`
- `git diff --check`

**Step 2: Update backlog/docs**

Зафиксировать:
- `B3.31a` closed / partially closed
- pragmatic compromises:
  - SQLite fallback store
  - snapshot currently stores document registry blob for resume
  - Postgres-backed production store остаётся follow-up only if not fully implemented here

**Step 3: Commit**

```bash
git add backend/orchestrator/state_store.py \
        backend/orchestrator/agent_api.py \
        backend/orchestrator/execution_runtime.py \
        backend/orchestrator/chainlit_app.py \
        backend/tests/test_state_store.py \
        backend/tests/test_execution_runtime.py \
        backend/tests/test_agent_api_orchestrate.py \
        backend/tests/test_chainlit_runtime_mode.py \
        backend/tests/test_chainlit_resume_state.py \
        TASKS.md \
        docs/plans/2026-03-11-unified-recovery-and-ui-plan.md \
        docs/plans/2026-03-11-orchestration-stabilization-plan.md \
        docs/plans/2026-03-12-execution-review-corrective-plan.md \
        docs/plans/2026-03-12-b331a-backend-state-store.md
git commit -m "feat(orchestrator): add backend-owned execution state store"
```
