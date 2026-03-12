# B3.35 + B3.36 Honest Tiers and Evidence UX Plan

**Goal:** Выравнять публичные tier labels с реальным runtime и довести citations/evidence UX до состояния, где пользователь видит не только quote/score, но и provenance `session | knowledge_base`, `display_name`, `collection_id`, `page/section` там, где метаданные доступны.

## Why now

После `B3.33` provenance metadata уже проходит через unified backend core, но продуктовая поверхность ещё не полностью использует её:
- в коде и docs всё ещё встречаются переобещающие `agentic / multi-agent` формулировки;
- evidence UX показывает provenance частично, но без явной продуктовой консолидации и без выравнивания labels/summary surface.

## Scope

### B3.35 — Honest tiers
- Привести документацию и публичные labels к честной схеме:
  - `Tier 1` = basic retrieval
  - `Tier 2` = corrective retrieval
  - `Tier 3` = iterative retrieval
  - `Tier 4` = planned multi-agent
- Не ломать внутренние runtime keys `simple | corrective | agentic | multi-agent`, если они уже участвуют в коде/UMS/tier-selector.
- Добавить compatibility wording там, где внутренний `agentic` по коду фактически означает iterative retrieval.

### B3.36 — Citations/evidence UX v2
- Довести UI/markdown rendering до явного evidence contract:
  - `display_name`
  - `chunk_id`
  - `source_origin`
  - `collection_id`
  - `page/section` если доступны
  - `excerpt`
  - `relevance`
- Показать `source_scope_summary` для mixed retrieval.
- Не переносить routing/policy decisions в UI.

## Tasks

### Task 1: Audit public tier/evidence surfaces

**Files**
- `backend/orchestrator/rag/pipeline.py`
- `backend/services/hardware/tier_selector.py`
- `backend/orchestrator/chainlit_app.py`
- `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
- `docs/plans/2026-03-12-execution-review-corrective-plan.md`
- `TASKS.md`

**Steps**
1. Найти public-facing wording, которое обещает полноценный `agentic`/`multi-agent`.
2. Разделить:
   - внутренние runtime keys, которые не трогаем без широкой миграции;
   - user/docs labels, которые можно и нужно выровнять сейчас.

### Task 2: Honest tiers implementation

**Files**
- `backend/services/hardware/tier_selector.py`
- `backend/orchestrator/rag/pipeline.py`
- тесты рядом при необходимости

**Steps**
1. Обновить docstrings/comments/help text до честной терминологии.
2. Если где-то есть public summary/status output, заменить `agentic` -> `iterative retrieval`, `multi-agent` -> `planned multi-agent`.
3. Не менять machine-readable `rag_mode` без отдельной migration phase.

### Task 3: Evidence UX v2

**Files**
- `backend/orchestrator/chainlit_app.py`
- `backend/tests/test_chainlit_runtime_mode.py`
- `backend/tests/test_document_analysis.py` при необходимости

**Steps**
1. Расширить render surface для doc-QA sources:
   - показывать `display_name`
   - `source_origin`
   - `collection_id`
   - `section/page`, если есть
2. Добавить summary line для `source_scope_summary`.
3. Не менять сам backend retrieval contract, только presentation/use of metadata.

### Task 4: Verification and docs sync

**Steps**
1. Прогнать targeted tests для Chainlit/doc-QA surface.
2. Прогнать `cd backend && pytest tests/ -q -m "not integration"`.
3. Синхронизировать `TASKS.md` и phase docs.

## Definition of Done

- User/docs labels больше не переобещают полноценный `multi-agent` runtime.
- Evidence block в doc-QA явно показывает provenance и retrieval scope там, где это возможно.
- Внутренние runtime keys не сломаны.
- `cd backend && pytest tests/ -q -m "not integration"` зелёный.

## Status

Completed on 2026-03-13.

### Delivered
- Honest tier wording added without breaking runtime keys:
  - `simple` -> basic retrieval
  - `corrective` -> corrective retrieval
  - `agentic` -> iterative retrieval
  - `multi-agent` -> planned multi-agent
- Public-facing surfaces updated:
  - `backend/services/hardware/tier_selector.py`
  - `backend/orchestrator/rag/pipeline.py`
  - `backend/orchestrator/ui_control_plane.py`
  - `backend/orchestrator/chainlit_app.py`
  - `backend/services/model_manager/unified_model_server.py`
  - `scripts/runtime_preflight.py`
- Evidence UX v2 now renders:
  - `display_name`
  - `source_origin`
  - `collection_id`
  - `section/page` when available
  - `retrieval_scope` / `source_scope_summary`

### Verification
- `pytest backend/tests/test_chainlit_runtime_mode.py backend/tests/test_runtime_preflight.py backend/tests/test_unified_model_server_startup.py backend/tests/test_rag_pipeline.py backend/tests/test_tier_selector.py -q`
- `cd backend && pytest tests/ -q -m "not integration"` -> `389 passed, 4 deselected`
- `python -m py_compile backend/services/hardware/tier_selector.py backend/orchestrator/rag/pipeline.py backend/orchestrator/chainlit_app.py backend/orchestrator/ui_control_plane.py backend/services/model_manager/unified_model_server.py scripts/runtime_preflight.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_runtime_preflight.py backend/tests/test_unified_model_server_startup.py`
- `git diff --check`
