# B3.33 Knowledge Base RAG Implementation Plan

**Goal:** Формально и технически разделить `session_rag` и `knowledge_base_rag`, не ломая backend-first orchestration. На выходе должен появиться backend-owned source registry с persisted KB chunks/metadata, merged retrieval policy для `knowledge_base + session overlay`, и явная provenance-модель `source_origin = session | knowledge_base`.

**Architecture:** Не делать full rewrite RAG. Использовать текущие building blocks:
- `AdaptiveRAGPipeline`
- `HybridRetriever`
- `orchestration_runtime` / `execution_runtime`
- `Chainlit` как control-plane и upload UI
- backend-owned store по аналогии с `state_store.py`

`knowledge_base_rag` не создаёт новый route. Он меняет retrieval contour, source registry и evidence contract.

**Status:** Completed on 2026-03-13 as V1 after `B3.34`. `LaBSE` остаётся dense retrieval baseline; `Qwen3` не переносится в retrieval автоматически.

## Implementation result

### Delivered in V1
- backend-owned KB source registry:
  - `kb_sources`
  - `kb_chunks`
  - dedup by `content_hash`
  - schema/versioning fields `index_version`, `embedding_model_id`, `chunking_version`
- ingestion helper for persistent KB text sources
- merged retrieval adapter:
  - `session_rag` -> active session docs only
  - `knowledge_base_rag` -> KB collection + session overlay
  - candidate budget per scope
  - dedup by normalized text
  - provenance in returned sources:
    - `source_origin`
    - `collection_id`
    - `display_name`
- unified backend integration:
  - `orchestration_runtime` routes KB-backed doc questions without fake upload-required
  - `execution_runtime` executes doc-QA through the same backend core for Chainlit/API
  - Chainlit/API dependency builders provide retrieval embed/store adapters instead of local KB paths

### Explicit V1 compromise
- KB retrieval пока строится transient over persisted KB chunks коллекции.
- Persisted embeddings / vector index / reranker intentionally deferred into follow-up hardening.

### Verification
- `pytest backend/tests/test_knowledge_base_store.py backend/tests/test_knowledge_base_ingestion.py backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_orchestration_runtime.py -q`
- `pytest backend/tests/test_knowledge_base_store.py backend/tests/test_knowledge_base_ingestion.py backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_orchestration_runtime.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
- `cd backend && pytest tests/ -q -m "not integration"` -> `386 passed, 4 deselected`
- `python -m py_compile backend/orchestrator/knowledge_base_store.py backend/orchestrator/knowledge_base_ingestion.py backend/orchestrator/knowledge_base_retrieval.py backend/orchestrator/execution_runtime.py backend/orchestrator/orchestration_runtime.py backend/orchestrator/agent_api.py backend/orchestrator/chainlit_app.py backend/tests/test_execution_runtime.py backend/tests/test_orchestration_runtime.py`
- `git diff --check`

## Scope

### In scope
- backend-owned KB source registry:
  - `kb_sources`
  - `kb_chunks`
  - `content_hash`
  - `index_version`
  - `embedding_model_id`
  - `chunking_version`
- API/runtime contract:
  - `rag_scope=session_rag|knowledge_base_rag`
  - `knowledge_collection_id`
  - `source_scope_summary`
- merged retrieval policy:
  - session-only path
  - knowledge-base-only path
  - knowledge-base + session overlay path
  - dedup + candidate budget + provenance
- tests for registry, retrieval merge and provenance

### Out of scope
- vector DB migration
- reranker rollout
- UI redesign beyond already existing `rag_scope` selector
- changing dense retrieval embedder from `LaBSE`

## Tasks

### Task 1: KB store and schema

**Files**
- Add: `backend/orchestrator/knowledge_base_store.py`
- Add: `backend/tests/test_knowledge_base_store.py`

**Steps**
1. Ввести backend-owned SQLite/Postgres-ready store для:
   - `kb_sources`
   - `kb_chunks`
2. Source fields:
   - `source_id`
   - `collection_id`
   - `display_name`
   - `content_hash`
   - `mime_type`
   - `status`
   - `index_version`
   - `embedding_model_id`
   - `chunking_version`
   - timestamps
3. Chunk fields:
   - `chunk_id`
   - `source_id`
   - `chunk_index`
   - `text`
   - `metadata_json`
   - `source_origin`
4. Поддержать dedup по `content_hash`.

### Task 2: KB ingestion helpers

**Files**
- Add: `backend/orchestrator/knowledge_base_ingestion.py`
- Add/Modify tests as needed

**Steps**
1. Добавить ingestion helper для text/doc payload:
   - parse text input
   - chunk через существующий chunker
   - register source/chunks in KB store
2. Пока не тащить embeddings в persisted DB как обязательный requirement.
   Retrieval в первой версии может строить transient in-memory retriever из KB chunks коллекции.
3. Зафиксировать это как осознанный V1 компромисс и follow-up в `TASKS.md`.

### Task 3: Merged retrieval adapter

**Files**
- Add: `backend/orchestrator/knowledge_base_retrieval.py`
- Modify: `backend/orchestrator/execution_runtime.py`
- Modify: `backend/orchestrator/agent_api.py`
- Modify: `backend/orchestrator/chainlit_app.py`
- Add/Modify tests

**Steps**
1. Ввести backend retrieval adapter, который принимает:
   - `rag_scope`
   - `knowledge_collection_id`
   - session docs / active docs
   - query
2. Реализовать policy:
   - `session_rag`: только session docs
   - `knowledge_base_rag` без overlay: только KB collection
   - `knowledge_base_rag` с overlay: KB + session chunks
3. Реализовать candidate budget:
   - отдельные candidate pools для KB и session
   - merged top-k
4. Реализовать dedup:
   - по `content_hash`/normalized text
5. Проставлять provenance:
   - `source_origin`
   - `collection_id`
   - `source_scope_summary`

### Task 4: Verification and docs sync

**Files**
- Modify: `TASKS.md`
- Modify: relevant plan docs if needed

**Steps**
1. Добавить tests:
   - KB store create/list/dedup
   - merged retrieval returns session + KB provenance
   - `source_scope_summary` корректен для:
     - `session`
     - `knowledge_base`
     - `knowledge_base+session_overlay`
2. Прогнать targeted suite и `cd backend && pytest tests/ -q -m "not integration"`.
3. Если V1 идёт без persisted embeddings, записать это как explicit follow-up, а не скрытый компромисс.
