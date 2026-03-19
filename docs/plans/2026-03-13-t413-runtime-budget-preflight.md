# T4.13 Runtime Budget And Preflight Profiles Implementation Plan

Status: implemented on 2026-03-13.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ввести backend-owned runtime context budgeting и preflight profiles без full rewrite launcher/runtime stack.

**Architecture:** `UMS` становится source of truth для runtime budget metadata (`runtime_profile`, `effective_context_tokens`, retrieved-context budget), а `orchestrator` и `Chainlit` перестают резать контекст по фиксированным символам. На этой фазе profile selection остаётся env-driven и backend-first; полноценный launcher/preflight pipeline и richer UI selector остаются для `T4.14/T4.3`.

**Tech Stack:** Python 3.11, FastAPI, Chainlit, pytest, existing UMS + AdaptiveRAGPipeline

**Outcome:** Goal achieved for the current architecture slice.
- `UMS /status` now exposes `runtime_profile`, `effective_context_tokens`, `retrieved_context_tokens_budget`, `generation_tokens_reserve`, `context_budget_ratio`.
- `AdaptiveRAGPipeline` computes retrieved context budget from token-derived runtime metadata instead of only fixed char caps.
- `Chainlit` reads runtime budget metadata from UMS, applies it during RAG reindex, and surfaces it in effective configuration summary.
- Full backend non-integration suite passed after integration.

**Pragmatic compromise kept intentionally:**
- runtime/preflight profile selection remains env-driven/backend-first in this phase; no user-facing selector yet;
- unified preflight script, `.env.runtime`, and launcher consolidation stay in `T4.14`.

---

### Task 1: Зафиксировать runtime budget model в UMS

**Files:**
- Modify: `backend/services/model_manager/unified_model_server.py`
- Test: `backend/tests/test_unified_model_server_startup.py`

**Step 1: Write the failing tests**

Покрыть:
- `/status` возвращает `runtime_profile`
- `/status` возвращает `effective_context_tokens`
- `/status` возвращает retrieved-context budget metadata
- `manual` profile уважает env override для context tokens

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_unified_model_server_startup.py -q`

**Step 3: Write minimal implementation**

Добавить в UMS:
- env-driven runtime profiles:
  - `default`
  - `adaptive`
  - `manual`
- helper `resolve_runtime_budget(...)`
- публикацию в `/status`:
  - `runtime_profile`
  - `effective_context_tokens`
  - `retrieved_context_tokens_budget`
  - `generation_tokens_reserve`
  - `context_budget_ratio`

Правила:
- `manual` использует `UMS_MANUAL_EFFECTIVE_CONTEXT_TOKENS`
- `adaptive` строит budget от `tier_config.llm_ctx_size`
- `default` использует safe default без hardware heuristics beyond existing tier config

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_unified_model_server_startup.py -q`

### Task 2: Перевести RAG pipeline с fixed chars на effective context budget

**Files:**
- Modify: `backend/orchestrator/rag/pipeline.py`
- Modify: `backend/orchestrator/chainlit_app.py`
- Test: `backend/tests/test_chainlit_runtime_mode.py`
- Create: `backend/tests/test_rag_runtime_budget.py`

**Step 1: Write the failing tests**

Покрыть:
- `AdaptiveRAGPipeline` умеет принимать `effective_context_tokens`
- budget конвертируется в char budget через явную эвристику, а не магическое число внутри loop
- retrieved context budget ограничивает assembled context
- `Chainlit` строит/обновляет RAG pipeline с runtime budget metadata из UMS status

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_rag_runtime_budget.py backend/tests/test_chainlit_runtime_mode.py -q`

**Step 3: Write minimal implementation**

Добавить:
- в `AdaptiveRAGPipeline`:
  - `effective_context_tokens`
  - `retrieved_context_ratio`
  - helper для вычисления `max_context_chars` из token budget
  - metadata в `RAGResult` о budget usage
- в `chainlit_app.py`:
  - чтение новых полей из UMS `/status`
  - helper для извлечения runtime budget
  - использование budget при создании/reindex `AdaptiveRAGPipeline`

Правило:
- retrieved context должен занимать 55-65% окна, default `0.60`
- если UMS budget недоступен, использовать documented safe fallback

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_rag_runtime_budget.py backend/tests/test_chainlit_runtime_mode.py -q`

### Task 3: Протянуть runtime profile metadata в control-plane summary без превращения UI в policy-layer

**Files:**
- Modify: `backend/orchestrator/ui_control_plane.py`
- Modify: `backend/orchestrator/chainlit_app.py`
- Test: `backend/tests/test_chainlit_runtime_mode.py`

**Step 1: Write the failing tests**

Покрыть:
- effective settings summary показывает runtime profile и effective context budget
- UI не выбирает raw budget, а только отображает backend-resolved metadata
- отсутствие UMS metadata не ломает summary/settings flow

**Step 2: Run tests to verify they fail**

Run:
- `pytest backend/tests/test_chainlit_runtime_mode.py -q -k "runtime budget or summary"`

**Step 3: Write minimal implementation**

Добавить:
- runtime budget metadata в session/effective summary
- backend-derived helper вроде `_get_runtime_budget_metadata()`
- summary block:
  - `runtime_profile`
  - `effective_context_tokens`
  - `retrieved_context_tokens_budget`

Не добавлять:
- новый mutable selector профиля в UI
- raw token budget inputs

**Step 4: Run tests to verify they pass**

Run:
- `pytest backend/tests/test_chainlit_runtime_mode.py -q`

### Task 4: Закрыть T4.13 verification и docs sync

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`

**Step 1: Run phase-level regression**

Run:
- `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_rag_runtime_budget.py backend/tests/test_chainlit_runtime_mode.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/services/model_manager/unified_model_server.py backend/orchestrator/rag/pipeline.py backend/orchestrator/chainlit_app.py backend/orchestrator/ui_control_plane.py`
- `git diff --check`

**Step 2: Update backlog/docs**

Зафиксировать:
- `T4.13` closed
- runtime profile selection пока env-driven/backend-owned
- `T4.14` остаётся phase для preflight script + `.env.runtime` + launcher consolidation
- `T4.3` остаётся phase для user-facing profile selector

**Step 3: Commit**

```bash
git add backend/services/model_manager/unified_model_server.py \
        backend/orchestrator/rag/pipeline.py \
        backend/orchestrator/chainlit_app.py \
        backend/orchestrator/ui_control_plane.py \
        backend/tests/test_unified_model_server_startup.py \
        backend/tests/test_rag_runtime_budget.py \
        backend/tests/test_chainlit_runtime_mode.py \
        TASKS.md \
        docs/plans/2026-03-11-unified-recovery-and-ui-plan.md \
        docs/plans/2026-03-12-execution-review-corrective-plan.md \
        docs/plans/2026-03-13-t413-runtime-budget-preflight.md
git commit -m "feat(runtime): add context budget and preflight profiles"
```
