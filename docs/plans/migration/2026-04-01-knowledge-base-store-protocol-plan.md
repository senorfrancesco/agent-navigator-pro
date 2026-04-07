# Knowledge Base Store Protocol Follow-up Plan

**Дата:** 2026-04-01  
**Статус:** Proposed  
**Связанные документы:**  
- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)  
- [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)  
- [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)  
- [MIGRATION_OVERIEW_WITH_LEAKS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_OVERIEW_WITH_LEAKS.md)  
- [2026-03-13-b333-knowledge-base-rag.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/2026-03-13-b333-knowledge-base-rag.md)  
- [2026-04-01-migration-readiness-pr-decision-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-migration-readiness-pr-decision-plan.md)

## 1. Назначение плана

- [ ] Зафиксировать отдельный follow-up slice под `KnowledgeBaseStoreProtocol`.
- [ ] Разорвать жёсткую типовую зависимость orchestration/retrieval кода от `SQLiteKnowledgeBaseStore`.
- [ ] Подготовить backend к будущей миграции `SQLiteKnowledgeBaseStore -> QdrantKnowledgeBaseStore`.
- [ ] Сделать это без одновременного смешивания:
  - [ ] UI migration в Open WebUI;
  - [ ] vector DB migration;
  - [ ] полного переразворота retrieval pipeline.

**Ключевая идея:**  
Сначала вводится **storage contract**, потом factory boundary, и только после этого появляется новая backend-реализация на Qdrant.  
Open WebUI не должен знать, какой storage backend стоит за retrieval-контуром.

---

## 2. Почему это отдельный шаг, а не часть Qdrant migration

### 2.1 Текущая проблема

- [ ] Сейчас код retrieval/ingestion в ряде мест типизирован через `SQLiteKnowledgeBaseStore`.
- [ ] Это означает, что переход на Qdrant в будущем будет не заменой backend implementation, а каскадной правкой call sites.
- [ ] Такая связность ухудшает migration path:
  - [ ] для Open WebUI tools;
  - [ ] для backend-owned retrieval state;
  - [ ] для переносимого knowledge base слоя.

### 2.2 Почему нельзя смешивать всё в один migration slice

- [ ] Одновременное изменение UI + tool contracts + storage backend + retrieval logic создаст слишком широкий риск.
- [ ] Это противоречит текущему migration-принципу:
  - [ ] explicit boundaries;
  - [ ] replaceable adapters;
  - [ ] no big-bang rewrite.

### 2.3 Что даёт `KnowledgeBaseStoreProtocol`

- [ ] Единый контракт для storage backend.
- [ ] Возможность оставить текущий SQLite backend без изменений в поведении.
- [ ] Возможность добавить `QdrantKnowledgeBaseStore` позже как drop-in backend.
- [ ] Изоляцию orchestration/retrieval слоя от конкретной persistence implementation.

---

## 3. Архитектурная цель

### 3.1 Целевая схема

- [ ] `Open WebUI / Chainlit / API client`
  -> [ ] `FastAPI backend / tool server`
  -> [ ] `retrieval + orchestration layer`
  -> [ ] `KnowledgeBaseStoreProtocol`
  -> [ ] `SQLiteKnowledgeBaseStore | QdrantKnowledgeBaseStore | hybrid adapter`

### 3.2 Нормативное правило

- [ ] Retrieval code не должен зависеть от concrete SQLite class.
- [ ] Ingestion code не должен зависеть от concrete SQLite class.
- [ ] Factory `get_knowledge_base_store()` должна оставаться каноническим entrypoint.
- [ ] Конкретная backend-реализация должна подменяться через factory/config, а не через правку call sites.

---

## 4. Scope

### In scope

- [ ] Добавить `KnowledgeBaseStoreProtocol` в [knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_store.py)
- [ ] Перевести type annotations в:
  - [ ] [knowledge_base_ingestion.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_ingestion.py)
  - [ ] [knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_retrieval.py)
- [ ] Обновить `get_knowledge_base_store()` так, чтобы возвращаемый тип был контрактом, а не SQLite class
- [ ] Добавить tests, подтверждающие совместимость текущего SQLite backend с protocol contract
- [ ] Зафиксировать ограничения и future requirements для Qdrant implementation

### Out of scope

- [ ] Реальная реализация `QdrantKnowledgeBaseStore`
- [ ] Замена SQLite на Qdrant в runtime
- [ ] Перенос retrieval на прямые Qdrant query calls
- [ ] UI migration в Open WebUI
- [ ] Переход на новую metadata DB
- [ ] Полная переработка `HybridRetriever`

---

## 5. Текущие touch points

### 5.1 Store layer

- [ ] [knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_store.py)
  - [ ] `KnowledgeBaseSourceRecord`
  - [ ] `KnowledgeBaseChunkRecord`
  - [ ] `SQLiteKnowledgeBaseStore`
  - [ ] `get_knowledge_base_store()`

### 5.2 Ingestion layer

- [ ] [knowledge_base_ingestion.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_ingestion.py)
  - [ ] `ingest_text_source_sync(..., store: Optional[SQLiteKnowledgeBaseStore] = None, ...)`

### 5.3 Retrieval layer

- [ ] [knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_retrieval.py)
  - [ ] `_build_kb_entries(kb_store: SQLiteKnowledgeBaseStore, ...)`
  - [ ] `retrieve_merged_chunks(..., kb_store: Optional[SQLiteKnowledgeBaseStore], ...)`

### 5.4 Related tests

- [ ] [test_knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_store.py)
- [ ] [test_knowledge_base_ingestion.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_ingestion.py)
- [ ] [test_knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_retrieval.py)
- [ ] Possibly:
  - [ ] [test_execution_runtime.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_execution_runtime.py)
  - [ ] [test_orchestration_runtime.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_orchestration_runtime.py)

---

## 6. Целевой контракт

### 6.1 Минимальный required interface

- [ ] Ввести `KnowledgeBaseStoreProtocol` с обязательными методами:
  - [ ] `register_source_sync(...) -> KnowledgeBaseSourceRecord`
  - [ ] `replace_chunks_sync(...) -> None`
  - [ ] `list_sources_sync(...) -> List[KnowledgeBaseSourceRecord]`
  - [ ] `list_chunks_sync(...) -> List[KnowledgeBaseChunkRecord]`
  - [ ] `register_source(...) -> KnowledgeBaseSourceRecord`
  - [ ] `replace_chunks(...) -> None`
  - [ ] `list_sources(...) -> List[KnowledgeBaseSourceRecord]`
  - [ ] `list_chunks(...) -> List[KnowledgeBaseChunkRecord]`

### 6.2 Contract requirements for embeddings

- [ ] Зафиксировать, что `chunk.embedding` должен возвращаться как `Optional[np.ndarray]`
- [ ] dtype должен быть `float32`
- [ ] shape должен быть `(embedding_dim,)`
- [ ] будущий Qdrant backend обязан выполнять `List[float] -> np.asarray(..., dtype=np.float32)` на чтении

### 6.3 Contract requirements for metadata

- [ ] `KnowledgeBaseSourceRecord` и `KnowledgeBaseChunkRecord` остаются каноническими transport/dataclass entities
- [ ] storage backend не должен менять их внешний shape
- [ ] orchestration/retrieval слой не должен знать details внутренней схемы SQLite или Qdrant payload layout

---

## 7. План реализации

## Phase A: Formalize protocol boundary

- [ ] Добавить `Protocol` и `@runtime_checkable` в [knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_store.py)
- [ ] Описать минимальный API-контракт store backend
- [ ] Обновить сигнатуру:
  - [ ] `get_knowledge_base_store() -> KnowledgeBaseStoreProtocol`

### Acceptance criteria

- [ ] Текущий SQLite store удовлетворяет protocol без изменения runtime behavior
- [ ] Никакая логика retrieval/ingestion ещё не меняется функционально

## Phase B: Retype call sites

- [ ] Обновить `knowledge_base_ingestion.py`
  - [ ] `store: Optional[KnowledgeBaseStoreProtocol]`
- [ ] Обновить `knowledge_base_retrieval.py`
  - [ ] `_build_kb_entries(kb_store: KnowledgeBaseStoreProtocol, ...)`
  - [ ] `retrieve_merged_chunks(..., kb_store: Optional[KnowledgeBaseStoreProtocol], ...)`
- [ ] Проверить другие возможные type references на `SQLiteKnowledgeBaseStore`

### Acceptance criteria

- [ ] В orchestration/retrieval code не остаётся type coupling к SQLite class, кроме самой SQLite implementation
- [ ] Call sites работают без изменения исполнения

## Phase C: Verification and regression safety

- [ ] Добавить tests, подтверждающие, что `SQLiteKnowledgeBaseStore` соответствует protocol
- [ ] Обновить tests ingestion/retrieval так, чтобы они принимали contract-level typing
- [ ] Проверить, что:
  - [ ] session + KB retrieval не деградировал
  - [ ] provenance не изменилась
  - [ ] dedup/payload shape не сломались

### Acceptance criteria

- [ ] Targeted tests зелёные
- [ ] `pytest backend/tests/ -q -m "not integration"` не получает regression в KB paths

## Phase D: Record Qdrant readiness assumptions

- [ ] Не писать сам Qdrant backend в этом slice
- [ ] Но зафиксировать в docs/TASKS:
  - [ ] что должен уметь будущий `QdrantKnowledgeBaseStore`
  - [ ] какие conversion rules обязательны
  - [ ] какие API surface должны остаться неизменными

### Acceptance criteria

- [ ] После завершения этого slice можно начинать отдельный plan на `QdrantKnowledgeBaseStore`
- [ ] Qdrant phase уже не требует перепроектирования retrieval call sites

---

## 8. Как это связано с Open WebUI migration

### 8.1 Что этот plan делает для Open WebUI

- [ ] Убирает жёсткую зависимость tool backend от SQLite-specific knowledge store
- [ ] Делает backend более пригодным как stable tool server
- [ ] Подготавливает storage boundary для self-hosted RAG architecture

### 8.2 Что этот plan не делает

- [ ] Не переводит сам UI на Open WebUI
- [ ] Не меняет tool contracts
- [ ] Не внедряет `/v1/files`
- [ ] Не делает Open WebUI основным UI прямо сейчас

### 8.3 Правильный порядок migration после этого шага

- [ ] `KnowledgeBaseStoreProtocol`
- [ ] `QdrantKnowledgeBaseStore`
- [ ] retrieval path adaptation for Qdrant
- [ ] Open WebUI tool/server wiring
- [ ] gradual UI migration away from Chainlit

---

## 9. Test plan

- [ ] `cd backend && pytest tests/test_knowledge_base_store.py tests/test_knowledge_base_ingestion.py tests/test_knowledge_base_retrieval.py -q`
- [ ] при необходимости:
  - [ ] `cd backend && pytest tests/test_execution_runtime.py tests/test_orchestration_runtime.py -q`
- [ ] финально:
  - [ ] `cd backend && pytest tests/ -q -m "not integration"`
- [ ] compile safety:
  - [ ] `python -m py_compile backend/orchestrator/knowledge_base_store.py backend/orchestrator/knowledge_base_ingestion.py backend/orchestrator/knowledge_base_retrieval.py`

---

## 10. Risks and non-goals

### Risks

- [ ] Слишком широкий protocol может зацементировать неудачный API
- [ ] Слишком узкий protocol потом не покроет Qdrant path
- [ ] Возможна путаница между:
  - [ ] storage contract
  - [ ] retrieval contract
  - [ ] vector search contract

### Non-goals

- [ ] Не проектировать сейчас весь future Qdrant API
- [ ] Не тащить async-only redesign store layer
- [ ] Не менять product behavior для KB RAG
- [ ] Не трогать `HybridRetriever` beyond compatibility

---

## 11. Итоговое нормативное решение

- [ ] `KnowledgeBaseStoreProtocol` — это обязательный архитектурный мост перед Qdrant migration
- [ ] Его нужно делать отдельным small/medium slice
- [ ] Его нельзя смешивать с Open WebUI UI migration
- [ ] После него можно безопасно проектировать `QdrantKnowledgeBaseStore` как drop-in backend, а не как второй параллельный мир
