# B3.31 Cleanup + Session Sync Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Закрыть остаток `B3.31`: убрать локальные routing-дубликаты из `chainlit_app.py` и сделать `pending_action` / `pending_route_choice` одним стабильным session-path без split-state.

**Architecture:** Не меняем orchestration boundary и не вводим новые модули. `chainlit_app.py` остаётся thin UI-adapter'ом: использует backend-owned routing helpers напрямую, а session-sync для pending-action централизуется в уже существующих helper-функциях.

**Tech Stack:** Chainlit adapter, backend orchestrator runtime, pytest, async unit tests, session-state mocks.

---

### Task 1: Удалить thin-wrapper routing duplicates из `chainlit_app.py`

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`
- Test: `backend/tests/test_chainlit_runtime_mode.py`

**Step 1: Write the failing test**

Добавить тест, который проверяет, что UI-path продолжает работать без локальных `_detect_intent`, `_is_social_query`, `_is_low_confidence`, `_build_route_choice_prompt`, `_build_route_choice_state`, `_resolve_pending_route_choice`, `_is_docs_summary_query`.

```python
def test_chainlit_app_uses_backend_routing_helpers_directly():
    import orchestrator.chainlit_app as module
    assert not hasattr(module, "_detect_intent")
    assert not hasattr(module, "_is_social_query")
    assert not hasattr(module, "_build_route_choice_prompt")
```

**Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_chainlit_runtime_mode.py -q -k "backend_routing_helpers_directly"
```

Expected:
- FAIL because legacy thin-wrapper helpers still exist.

**Step 3: Write minimal implementation**

В `backend/orchestrator/chainlit_app.py`:

- удалить thin-wrapper helper definitions:
  - `_detect_intent`
  - `_has_any_keyword`
  - `_is_social_query`
  - `_is_low_confidence`
  - `_get_last_session_docs`
  - `_build_route_choice_prompt`
  - `_build_route_choice_state`
  - `_resolve_pending_route_choice`
  - `_is_docs_summary_query`
- оставить только те helper’ы, которые реально UI-owned (`_is_report_query`, `_get_classifier_result`, session helpers, render helpers)
- заменить все call sites на уже импортированные backend aliases:
  - `_backend_resolve_pending_action_selection(...)`
  - `_backend_build_route_choice_state(...)`
  - `_backend_build_route_choice_prompt(...)`
  - `_backend_detect_intent(...)` only if still really needed after cleanup

**Step 4: Run tests to verify it passes**

Run:

```bash
cd backend && pytest tests/test_chainlit_runtime_mode.py -q
```

Expected:
- PASS

---

### Task 2: Централизовать sync-path для `pending_action` / `pending_route_choice`

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`
- Test: `backend/tests/test_chainlit_runtime_mode.py`
- Update: `TASKS.md`

**Step 1: Write the failing tests**

Добавить тесты на два сценария:

```python
def test_set_pending_route_choice_writes_both_session_keys():
    module._set_pending_route_choice({"type": "choose_route"})
    assert store["pending_action"] == {"type": "choose_route"}
    assert store["pending_route_choice"] == {"type": "choose_route"}
```

```python
def test_apply_session_state_patch_clears_both_pending_keys():
    store["pending_action"] = {"type": "choose_route"}
    store["pending_route_choice"] = {"type": "choose_route"}
    module._apply_session_state_patch({"pending_action": None})
    assert store["pending_action"] is None
    assert store["pending_route_choice"] is None
```

**Step 2: Run tests to verify they fail if sync is incomplete**

Run:

```bash
cd backend && pytest tests/test_chainlit_runtime_mode.py -q -k "pending_route_choice or session_state_patch_clears"
```

Expected:
- PASS or FAIL depending on current state; if already green, treat this as regression harness and proceed.

**Step 3: Write minimal implementation**

В `backend/orchestrator/chainlit_app.py`:

- оставить `_get_pending_route_choice()` и `_set_pending_route_choice()` единственным API для pending-choice session state
- убедиться, что все direct `cl.user_session.set("pending_action", ...)` / `set("pending_route_choice", ...)` outside helpers отсутствуют
- в `_apply_session_state_patch(...)` использовать `_set_pending_route_choice(...)` как единственную точку применения pending patch
- при route-choice timeout / non-choice input / explicit cancel использовать только `_set_pending_route_choice(None)`

В `TASKS.md`:

- обновить `B3.31-session-sync`, если кодовая часть будет закрыта
- если останется только resume-specific sync tail, сузить follow-up формулировку

**Step 4: Run tests to verify it passes**

Run:

```bash
cd backend && pytest tests/test_chainlit_runtime_mode.py -q
```

Expected:
- PASS

---

### Task 3: Прогнать cleanup-level verification и зафиксировать статус

**Files:**
- Modify: `TASKS.md`
- Optional: `docs/plans/2026-03-12-overnight-fix-manifest.md` (только если статус нужно синхронизировать)

**Step 1: Run targeted verification**

Run:

```bash
cd backend && pytest \
  tests/test_chainlit_runtime_mode.py \
  tests/test_agent_api_openai_compat.py \
  tests/test_agent_api_orchestrate.py \
  tests/test_execution_runtime.py \
  tests/test_orchestration_runtime.py -q
```

Expected:
- PASS

**Step 2: Run phase-level regression suite**

Run:

```bash
pytest backend/tests/test_agent_api_openai_compat.py \
       backend/tests/test_agent_api_orchestrate.py \
       backend/tests/test_execution_runtime.py \
       backend/tests/test_orchestration_runtime.py \
       backend/tests/test_chainlit_runtime_mode.py \
       backend/tests/test_document_analysis.py \
       backend/tests/test_chainlit_streaming.py \
       backend/tests/test_unified_model_server_streaming.py \
       backend/tests/test_unified_model_server_startup.py -q
```

Expected:
- PASS on changed surface

**Step 3: Run sanity checks**

Run:

```bash
git diff --check
python -m py_compile backend/orchestrator/chainlit_app.py
```

Expected:
- no diff check errors
- successful compile

**Step 4: Update backlog**

В `TASKS.md`:

- если cleanup и session-sync реально закрыты, отметить это в `B3.31`
- если останется tail, оставить только конкретный residual risk, без общего “cleanup” wording
- отдельно не трогать `B3.31a`
