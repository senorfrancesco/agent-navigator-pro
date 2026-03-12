# B3.33a Knowledge Base Retrieval Hardening Plan

**Goal:** довести `knowledge_base_rag` от V1 transient merged retrieval до более устойчивого production-oriented contour, не ломая backend-first execution core и не переписывая RAG стек.

## Why now

`B3.33` уже дал рабочий KB source registry, ingestion helper и unified merged retrieval. Но текущая реализация всё ещё intentionally компромиссная:
- retrieval строится transient over persisted chunks;
- score fusion между `session` и `knowledge_base` остаётся простым;
- tie-break для дубликатов и конфликтующих evidence chunks минимальный;
- rerank stage отсутствует.

Это уже достаточно для product V1, но пока ещё не production-hardening.

## Scope

### In scope
- явная merged shortlist policy для `session` vs `knowledge_base`
- score fusion / tie-break rules
- deterministic provenance preference when duplicates come from both scopes
- optional persisted dense index scaffolding для KB collections без миграции всего RAG
- tests for duplicate-heavy and mixed-scope retrieval behavior

### Out of scope
- full vector DB migration
- automatic switch from `LaBSE` to `Qwen3` for retrieval
- full reranker rollout, если для него ещё нет стабильного adapter path
- UI redesign beyond existing evidence surface

## Execution order

### Step 1: shortlist hardening

**Files**
- `backend/orchestrator/knowledge_base_retrieval.py`
- `backend/tests/test_knowledge_base_retrieval.py`

**Work**
- ввести более явную candidate merge policy:
  - per-scope candidate budgets
  - normalized score fusion
  - deterministic tie-break when `session` и `knowledge_base` имеют одинаковый normalized text
- preference policy:
  - если текст-дубликат найден и в `session`, и в `knowledge_base`, по умолчанию оставлять `session` как более свежий overlay
  - но сохранять higher raw score, если разница существенная
- добавить tests:
  - duplicate-heavy merged retrieval
  - session overlay beats KB duplicate
  - KB-only retrieval remains stable

### Step 2: persisted retrieval scaffold

**Files**
- `backend/orchestrator/knowledge_base_store.py`
- `backend/orchestrator/knowledge_base_ingestion.py`
- `backend/tests/test_knowledge_base_store.py`

**Work**
- добавить schema hooks для persisted dense retrieval metadata:
  - `embedding_blob` или отдельную таблицу `kb_chunk_embeddings` only if lightweight enough
  - `embedding_dim`
  - `embedding_model_id`
- если persisted embeddings окажутся слишком тяжёлыми для текущего scope, ограничиться explicit schema plan и follow-up, не делая half-baked storage

### Step 3: optional rerank hook

**Files**
- `backend/orchestrator/knowledge_base_retrieval.py`
- `backend/tests/test_knowledge_base_retrieval.py`

**Work**
- подготовить hook для rerank stage после merged shortlist
- по умолчанию оставить disabled, если нет stable scorer
- важно: hook должен быть backend-owned и не вылезать в UI

## Verification

- `pytest backend/tests/test_knowledge_base_retrieval.py backend/tests/test_knowledge_base_store.py -q`
- `pytest backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q`
- `cd backend && pytest tests/ -q -m "not integration"`
- `python -m py_compile backend/orchestrator/knowledge_base_retrieval.py backend/orchestrator/knowledge_base_store.py backend/orchestrator/knowledge_base_ingestion.py`
- `git diff --check`

## Definition of Done

- merged retrieval policy больше не является implicit/simple concat+dedup;
- duplicate handling между `session` и `knowledge_base` deterministic и покрыт тестами;
- KB retrieval hardening documented honestly;
- full backend non-integration suite остаётся зелёным.

## Status

In progress on 2026-03-13.

### Delivered first slice
- deterministic shortlist hardening added in `knowledge_base_retrieval.py`
- duplicate policy now prefers `session` overlay over KB duplicate when scores are near
- targeted verification:
  - `pytest backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"` -> `389 passed, 4 deselected`

### Remaining
- persisted embeddings / vector index for KB collections
- optional rerank hook
- broader score fusion policy beyond duplicate tie-break
