# T4.2 Chainlit UX Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ужесточить Chainlit UX поверх уже стабилизированного backend contract так, чтобы новый чат, список тредов, восстановление треда и текущий document/runtime context были прозрачными для пользователя без возврата routing/policy logic в UI.

**Architecture:** `Chainlit` остаётся thin control surface над backend execution/store. Улучшения ограничены presentation/state-sync слоем: thread naming/metadata, welcome/resume status messaging, unified context summary, и тестируемый resume UX. Все route/execution decisions по-прежнему идут из backend.

**Tech Stack:** Chainlit 2.x, FastAPI orchestrator, SQLite/Postgres-compatible state store abstraction, pytest.

---

## Status

Implemented on `2026-03-13`.

Что реально вошло в фазу:
- thread title/metadata sync через Chainlit data layer `update_thread(...)`;
- welcome/status message для нового чата;
- backend-snapshot-first resume status message с явным current context summary;
- summary по active docs / `rag_scope` / runtime profile / pending action;
- regression coverage в `backend/tests/test_chainlit_runtime_mode.py`.

Что сознательно не входило:
- новый backend routing;
- knowledge-base retrieval implementation;
- raw model selector;
- cosmetic redesign Chainlit shell.

### Task 1: Зафиксировать UX contract для start/resume/context summary

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- Modify: `docs/plans/2026-03-12-execution-review-corrective-plan.md`
- Modify: `docs/plans/2026-03-13-t42-chainlit-ux-hardening.md`

**Step 1: Уточнить канонический scope**

Зафиксировать, что в `T4.2` входят:
- thread title/metadata sync для списка тредов Chainlit;
- welcome/status message для нового чата;
- unified resume status message для восстановленного треда;
- видимый summary текущего active context:
  - active docs;
  - rag scope;
  - runtime/model profile;
  - pending action state.

**Step 2: Явно исключить out-of-scope**

Не включать в `T4.2`:
- новый backend routing;
- KB retrieval implementation;
- raw model-id selector;
- Open WebUI-specific UX;
- cosmetic-only redesign без state contract.

### Task 2: Написать падающие тесты для UX hardening

**Files:**
- Modify: `backend/tests/test_chainlit_runtime_mode.py`

**Step 1: Добавить тесты на status messaging**

Покрыть:
- `on_chat_start()` отправляет welcome/status message после settings panel;
- `on_chat_resume()` при backend snapshot отправляет resume status message;
- resume status message отражает:
  - число восстановленных активных документов;
  - `rag_scope`;
  - `assistant_mode`;
  - наличие `pending_action`.

**Step 2: Добавить тесты на thread metadata sync**

Покрыть:
- helper синхронизации thread metadata вызывает Chainlit data layer `update_thread(...)`;
- metadata включает `assistant_mode`, `rag_scope`, `runtime_mode`, `active_doc_ids`, `has_pending_action`;
- name треда берётся из первого user prompt или fallback summary.

**Step 3: Добавить тесты на context summary block**

Покрыть:
- формат summary для нового чата без документов;
- формат summary для document-aware чата;
- summary не ломается при пустом runtime budget metadata.

**Step 4: Запустить целевой файл**

Run: `pytest backend/tests/test_chainlit_runtime_mode.py -q`
Expected: падения по новым UX assertions.

### Task 3: Реализовать thread metadata/title sync в Chainlit adapter

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`

**Step 1: Добавить helper-ы thread metadata**

Нужны helper-ы:
- `_build_thread_metadata()`
- `_derive_thread_name_from_message(...)`
- `_sync_thread_presentation(...)`

`_build_thread_metadata()` должен включать:
- `assistant_mode`
- `runtime_mode`
- `rag_scope`
- `knowledge_collection_id`
- `active_doc_ids`
- `active_doc_labels`
- `has_pending_action`
- `last_route`
- `last_executor`

**Step 2: Синхронизировать metadata в lifecycle**

Вызывать `_sync_thread_presentation(...)` в:
- `on_chat_start()`
- `on_chat_resume()`
- `on_settings_update()`
- после `_render_execution_response(...)`
- после успешной загрузки документов / смены активного набора

**Step 3: Обновлять thread title**

Правило:
- если тред ещё без содержательного имени, использовать первую user message;
- при document-heavy старте можно fallback’ить на:
  - `Specific Tasks — <doc label>`
  - `RAG Q&A — <doc label>`
- не переименовывать тред на каждом сообщении, если имя уже задано.

### Task 4: Реализовать unified welcome/resume/context UX

**Files:**
- Modify: `backend/orchestrator/chainlit_app.py`

**Step 1: Добавить summary builders**

Нужны helper-ы:
- `_build_context_status_markdown(...)`
- `_build_welcome_markdown()`
- `_build_resume_markdown(...)`

Содержимое summary:
- active docs / active set status;
- `assistant_mode`, `runtime_mode`, `rag_scope`;
- `resolved_model_id` и runtime profile;
- `pending_action` state;
- при `knowledge_base_rag` выбранная collection;
- при отсутствии docs честный empty-state.

**Step 2: Welcome message**

В `on_chat_start()`:
- после `ChatSettings` отправить компактный welcome/status message;
- сослаться на starter cards и current effective config;
- не дублировать длинные инструкции.

**Step 3: Resume message**

В `on_chat_resume()`:
- после backend snapshot restore или legacy restore отправить явный resume status message;
- различать:
  - backend snapshot restored;
  - legacy history fallback restored;
  - nothing restored / empty thread.

**Step 4: Pending-action UX**

Если восстановлен `pending_action`, resume message должен явно сказать, что в треде есть незавершённый выбор/действие.

### Task 5: Verification и backlog sync

**Files:**
- Modify: `TASKS.md`
- Modify: `docs/plans/2026-03-13-t42-chainlit-ux-hardening.md`

**Step 1: Прогнать узкие тесты**

Run:
- `pytest backend/tests/test_chainlit_runtime_mode.py -q`
- `pytest backend/tests/test_agent_api_orchestrate.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_document_analysis.py -q`

**Step 2: Прогнать фазовый regression suite**

Run:
- `cd backend && pytest tests/ -q -m "not integration"`

**Step 3: Проверить синтаксис**

Run:
- `python -m py_compile backend/orchestrator/chainlit_app.py backend/tests/test_chainlit_runtime_mode.py`
- `git diff --check`

**Step 4: Обновить статус**

В `TASKS.md`:
- отметить `T4.2` как выполненный или частично выполненный;
- если остаются UX workaround’ы, записать их как отдельные follow-up.

В plan-doc:
- зафиксировать implemented status и остаточные риски.

### Task 6: Internal code review

**Files:**
- Review: `backend/orchestrator/chainlit_app.py`
- Review: `backend/tests/test_chainlit_runtime_mode.py`

**Step 1: Проверить инварианты**

Подтвердить:
- UI не принимает route/execution decisions;
- thread metadata sync не влияет на orchestration policy;
- welcome/resume UX не ломает headless tests;
- resume path остаётся backend-store-first.

**Step 2: Зафиксировать follow-up, если нужен**

Если выявятся проблемы:
- большие markdown summaries;
- лишний chat noise;
- слабый thread naming heuristic;

записать это в `TASKS.md` как follow-up, а не смешивать с текущей фазой.

## Verification

Выполнено:
- `pytest backend/tests/test_chainlit_runtime_mode.py -q`
- `pytest backend/tests/test_agent_api_orchestrate.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_document_analysis.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/orchestrator/chainlit_app.py backend/tests/test_chainlit_runtime_mode.py`
- `git diff --check`
