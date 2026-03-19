# B3.31b Production State Store Hardening

**Goal:** усилить backend-owned orchestration state store до production-safe уровня без full rewrite execution core.

## Safe slice

На этой фазе не строим event sourcing, queueing или новый orchestration platform. Нужен ровно такой practical hardening:

1. реальный `Postgres` store path alongside `SQLite` fallback
2. atomic optimistic locking inside store writes
3. explicit idempotency semantics for repeated workflow starts/submissions
4. slimmer persisted `resume_state_blob` через `document_refs`, а не inline document-heavy payload

## Context

`B3.31a` уже сделал backend-owned store через `orchestrator_runs`, но:

- implementation фактически `SQLite`-only
- `expected_version` проверяется до `UPDATE`, а не atomically inside SQL
- `idempotency_key` хранится, но не участвует в reuse semantics
- `resume_state_blob` всё ещё тянет `session_docs`/`documents_by_id`-style payload

Это достаточно для dev/runtime stabilization, но ещё не честный production-hardening.

## In scope

- `backend/orchestrator/state_store.py`
- `backend/orchestrator/execution_runtime.py`
- `backend/orchestrator/chainlit_app.py`
- tests around store/execution/resume
- docs/TASKS sync

## Out of scope

- event log / audit stream
- background workers / scheduler
- full document service rewrite
- removal of Chainlit fallback resume
- distributed locking beyond single-row optimistic concurrency

## Target changes

### 1. Store factory and Postgres path

Add:

- `PostgresOrchestrationStateStore`
- explicit store selection in `get_orchestration_state_store()`

Rules:

- `sqlite://...` -> `SQLiteOrchestrationStateStore`
- `postgres://...` / `postgresql://...` -> `PostgresOrchestrationStateStore`
- unsupported URL -> explicit error, not silent fallback
- if Postgres URL requested but driver unavailable -> explicit error

Implementation constraint:

- optional dependency path is acceptable
- tests may monkeypatch driver module instead of requiring live Postgres

### 2. Atomic optimistic locking

Current read-then-update must be replaced by atomic SQL semantics:

- `UPDATE ... SET ..., version = version + 1 WHERE run_id = ? AND version = ?`
- if `rowcount == 0`, raise version conflict

Need identical semantics for SQLite and Postgres.

### 3. Minimal idempotency semantics

Need real reuse semantics for `idempotency_key`:

- when `idempotency_key` is present, store should reuse existing run for the same `(workflow_type, idempotency_key)`
- behavior for same `thread_id/session_id` with different `idempotency_key` must be explicit and tested

Safe policy for this slice:

- prefer idempotency-key lookup first
- otherwise fallback to current thread/session locator behavior

### 4. Slim resume payload via document refs

Persisted `resume_state_blob` should stop storing full inline document payload.

Persist only:

- `active_doc_ids`
- `document_refs`
  - `document_id`
  - `display_name`
  - `source_origin`
  - `collection_id` if present
  - `path` only if current runtime needs it for safe resume
- `control_plane_state`
- `effective_settings`
- `pending_action`
- `active_mode`
- `last_route`
- `last_executor`
- `last_trace_id`

Do not persist:

- inline `documents_by_id` content blobs
- full `session_docs` text payload

`Chainlit` may still keep richer UI cache in session, but backend persisted state should become slimmer and more backend-shaped.

## File plan

### Modify

- `backend/orchestrator/state_store.py`
- `backend/orchestrator/execution_runtime.py`
- `backend/orchestrator/chainlit_app.py`
- `backend/tests/test_state_store.py`
- `backend/tests/test_execution_runtime.py`
- `backend/tests/test_chainlit_runtime_mode.py`
- `backend/tests/conftest.py`
- `TASKS.md`

### Add if useful

- `backend/tests/test_state_store_factory.py`

## Execution order

1. Add failing tests for store factory/idempotency/atomic versioning
2. Implement store selection + Postgres store path
3. Add failing tests for slim `document_refs` resume shape
4. Implement `document_refs`-based persisted snapshot in execution/runtime + Chainlit restore
5. Run targeted tests
6. Run full backend non-integration suite
7. Sync `TASKS.md`

## Verification

- `pytest backend/tests/test_state_store.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/orchestrator/state_store.py backend/orchestrator/execution_runtime.py backend/orchestrator/chainlit_app.py backend/tests/test_state_store.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py`
- `git diff --check`

## Definition of Done

- Postgres DSN no longer silently falls back to SQLite
- store writes use atomic optimistic locking
- `idempotency_key` has tested reuse semantics
- persisted resume blob uses `document_refs` instead of document-heavy inline payload
- Chainlit restore remains functional on backend snapshot roundtrip

## Completed

- Added explicit store selection:
  - `sqlite://...` -> `SQLiteOrchestrationStateStore`
  - `postgres://...` / `postgresql://...` -> `PostgresOrchestrationStateStore`
  - unsupported scheme -> `StateStoreConfigurationError`
- Added atomic optimistic locking for run updates via `WHERE version = ...`
- Added tested idempotency-key run reuse semantics
- Slimmed persisted resume payload from inline document blobs to `document_refs`
- Kept restore backward-compatible for legacy persisted `documents_by_id` snapshots

## Verification

- `pytest backend/tests/test_state_store.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
- `cd backend && pytest tests/ -q -m "not integration"` -> `416 passed, 4 deselected`
- `python -m py_compile backend/orchestrator/state_store.py backend/orchestrator/execution_runtime.py backend/orchestrator/chainlit_app.py backend/orchestrator/agent_api.py backend/tests/test_state_store.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py`
- `git diff --check`

## Completed status

Completed as the intended safe slice.
