# Open WebUI Migration Sprint Plan

**Дата:** 2026-04-01
**Статус:** Proposed
**Формат:** agent-ready sprint breakdown
**Связанные документы:**
- [2026-04-01-knowledge-base-store-protocol-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-knowledge-base-store-protocol-plan.md)
- [2026-04-01-qdrant-knowledge-base-store-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md)
- [2026-04-01-openwebui-tool-server-integration-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-openwebui-tool-server-integration-plan.md)
- [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)
- [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)
- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)

## 1. Цель sprint-плана

- [ ] Разбить migration roadmap на короткие, исполнимые slices.
- [ ] Дать структуру, которую можно напрямую раздавать агентам.
- [ ] Исключить `big-bang rewrite`.
- [ ] Зафиксировать зависимости между slices, чтобы агенты не полезли в конфликтующие зоны одновременно.

**Ключевой принцип:**
Сначала стабилизируем backend boundaries, потом меняем storage backend, потом подключаем новый UI/tool-server path.

---

## 2. Порядок спринтов

### Sprint 1

- [ ] `KnowledgeBaseStoreProtocol`
- [ ] минимальный storage contract
- [ ] zero-behavior-change refactor

### Sprint 2

- [ ] `QdrantKnowledgeBaseStore`
- [ ] payload schema
- [ ] filtered dense retrieval for KB path

### Sprint 3

- [ ] tool contracts
- [ ] upload/document binding
- [ ] Open WebUI integration path

### Sprint 4

- [ ] Open WebUI transition hardening
- [ ] hybrid retrieval hardening
- [ ] deprecation decisions for legacy paths

---

## 3. Sprint 1: KnowledgeBaseStoreProtocol

### Sprint goal

- [ ] Убрать типовую зависимость retrieval/ingestion кода от `SQLiteKnowledgeBaseStore`.
- [ ] Подготовить backend к Qdrant без изменения текущего поведения.

### Почему этот sprint первый

- [ ] Это наименьший риск.
- [ ] Это не меняет UI.
- [ ] Это не меняет retrieval semantics.
- [ ] Это снижает стоимость Sprint 2.

### Agent slices

#### Agent A1: Store protocol contract

- [ ] Добавить `KnowledgeBaseStoreProtocol` в [backend/orchestrator/knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_store.py)
- [ ] Добавить `Protocol` / `@runtime_checkable`
- [ ] Обновить `get_knowledge_base_store()` return typing

**Файлы:**
- [backend/orchestrator/knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_store.py)
- [backend/tests/test_knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_store.py)

**Acceptance criteria:**
- [ ] SQLite store соответствует protocol
- [ ] public contract зафиксирован явно
- [ ] runtime behavior не изменился

#### Agent A2: Ingestion/retrieval retyping

- [ ] Перевести type annotations в:
  - [ ] [backend/orchestrator/knowledge_base_ingestion.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_ingestion.py)
  - [ ] [backend/orchestrator/knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_retrieval.py)
- [ ] Убрать direct typing на `SQLiteKnowledgeBaseStore` в orchestration-facing коде

**Файлы:**
- [backend/orchestrator/knowledge_base_ingestion.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_ingestion.py)
- [backend/orchestrator/knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_retrieval.py)
- [backend/tests/test_knowledge_base_ingestion.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_ingestion.py)
- [backend/tests/test_knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_retrieval.py)

**Acceptance criteria:**
- [ ] нет regression в KB ingestion/retrieval
- [ ] type coupling к SQLite убран из retrieval-layer

### Sprint 1 verification

- [ ] `cd backend && pytest tests/test_knowledge_base_store.py tests/test_knowledge_base_ingestion.py tests/test_knowledge_base_retrieval.py -q`
- [ ] `cd backend && pytest tests/ -q -m "not integration"`
- [ ] `python -m py_compile backend/orchestrator/knowledge_base_store.py backend/orchestrator/knowledge_base_ingestion.py backend/orchestrator/knowledge_base_retrieval.py`

### Sprint 1 done definition

- [ ] `KnowledgeBaseStoreProtocol` в коде
- [ ] retrieval/ingestion работают через protocol boundary
- [ ] можно начинать `QdrantKnowledgeBaseStore` без перепроектирования call sites

---

## 4. Sprint 2: QdrantKnowledgeBaseStore

### Sprint goal

- [ ] Добавить Qdrant как реальный vector backend для knowledge base.
- [ ] Перевести KB retrieval на payload-filtered vector search вместо full collection scan.

### Зависимость

- [ ] Sprint 2 начинается только после завершения Sprint 1.

### Agent slices

#### Agent B1: Qdrant store skeleton + payload schema

- [ ] Добавить новый backend store:
  - [ ] `QdrantKnowledgeBaseStore`
- [ ] Зафиксировать payload schema
- [ ] Зафиксировать point ID strategy
- [ ] Реализовать write/update/list contract

**Файлы:**
- [backend/orchestrator/knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_store.py) или новый модуль рядом
- [backend/tests/test_knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_store.py)
- новый targeted test file под Qdrant backend

**Acceptance criteria:**
- [ ] источник/чанки пишутся и читаются через protocol
- [ ] payload schema стабильна
- [ ] chunk IDs детерминированы

#### Agent B2: Qdrant search contract

- [ ] Добавить `search_chunks_sync(...)` / `search_chunks(...)`
- [ ] Реализовать dense retrieval по query embedding
- [ ] Реализовать filter contract для:
  - [ ] `collection_id`
  - [ ] `document_id`
  - [ ] `source_origin`
  - [ ] `doc_type`
  - [ ] `language`

**Файлы:**
- Qdrant backend module
- tests на filtering и dense retrieval

**Acceptance criteria:**
- [ ] KB retrieval не читает всю коллекцию в память
- [ ] shortlist приходит из vector DB query

#### Agent B3: knowledge_base_retrieval integration

- [ ] Подключить `search_chunks(...)` в [backend/orchestrator/knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_retrieval.py)
- [ ] Оставить session overlay локальным
- [ ] Сохранить:
  - [ ] dedup
  - [ ] provenance
  - [ ] rerank hook
  - [ ] `source_scope_summary`

**Файлы:**
- [backend/orchestrator/knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_retrieval.py)
- [backend/tests/test_knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_knowledge_base_retrieval.py)
- possibly:
  - [backend/tests/test_execution_runtime.py](/home/seral/HDD/proj/agent-navigator-pro/backend/tests/test_execution_runtime.py)

**Acceptance criteria:**
- [ ] `knowledge_base_rag` идёт через Qdrant shortlist
- [ ] `knowledge_base + session overlay` не сломан
- [ ] session-only path не сломан

### Sprint 2 verification

- [ ] `cd backend && pytest tests/test_knowledge_base_store.py tests/test_knowledge_base_retrieval.py -q`
- [ ] `cd backend && pytest tests/test_execution_runtime.py tests/test_orchestration_runtime.py -q`
- [ ] `cd backend && pytest tests/ -q -m "not integration"`

### Sprint 2 done definition

- [ ] KB retrieval использует Qdrant как vector backend
- [ ] `knowledge_base_retrieval.py` остаётся policy/merge layer
- [ ] можно переходить к UI/tool-server migration без перепроектирования retrieval

---

## 5. Sprint 3: Tool contracts + upload/document binding + Open WebUI path

### Sprint goal

- [ ] Подключить `Open WebUI` к backend как к tool server.
- [ ] Сделать backend-owned upload/document binding model.
- [ ] Сохранить `Chainlit` как transitional UI.

### Зависимость

- [ ] Sprint 3 начинается только после завершения Sprint 2 или после осознанного решения запускать Open WebUI на SQLite-backed KB path временно.

### Agent slices

#### Agent C1: Tool contracts

- [ ] Ввести узкие backend endpoints под tools:
  - [ ] `ask_document`
  - [ ] `analyze_document_fast/deep`
  - [ ] `compare_documents_fast/deep`
  - [ ] `analyze_equipment_fast/deep`
- [ ] Не тащить обязательный classifier-first path в manual-first scenario

**Файлы:**
- [backend/orchestrator/agent_api.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/agent_api.py)
- new tool contract schemas
- tests for tool endpoints

**Acceptance criteria:**
- [ ] каждый tool имеет явный request/response contract
- [ ] tools исполняются через общий backend execution core

#### Agent C2: Upload/document binding

- [ ] Реализовать backend-owned upload model
- [ ] Добавить document binding:
  - [ ] `document_id`
  - [ ] `version_id`
  - [ ] attach/detach/activate/deactivate
- [ ] Сохранить session overlay semantics

**Файлы:**
- [backend/orchestrator/agent_api.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/agent_api.py)
- related document/session modules
- tests for upload + binding

**Acceptance criteria:**
- [ ] документы можно загрузить не через `Chainlit`-specific path
- [ ] backend знает, какие документы активны в чате/thread

#### Agent C3: Open WebUI integration

- [ ] Подключить Open WebUI к новым backend tools
- [ ] Проверить basic flows:
  - [ ] grounded doc QA
  - [ ] compare
  - [ ] equipment
- [ ] Не удалять `Chainlit`

**Файлы:**
- Open WebUI integration docs/config
- backend compatibility surfaces if needed

**Acceptance criteria:**
- [ ] Open WebUI может вызывать backend tools
- [ ] основная бизнес-логика не дублируется в UI

### Sprint 3 verification

- [ ] targeted API tests for tool contracts
- [ ] upload/document binding tests
- [ ] smoke checks against Open WebUI integration path

### Sprint 3 done definition

- [ ] Open WebUI работает поверх backend tools
- [ ] upload path не зависит от `Chainlit`
- [ ] `Chainlit` остаётся запасным transitional UI

---

## 6. Sprint 4: Hardening и transition cleanup

### Sprint goal

- [ ] Дожать качество retrieval и transition behavior.
- [ ] Принять решение по дальнейшей роли `Chainlit`.

### Candidate work

- [ ] hybrid/sparse hardening
- [ ] rerank optimization
- [ ] OpenAI-compatible path review
- [ ] deprecation plan for `Chainlit`-specific legacy surfaces
- [ ] UX validation for Open WebUI path

### Done definition

- [ ] Open WebUI path operationally устойчив
- [ ] `Chainlit` можно оставлять как legacy/transitional layer без критической доменной роли

---

## 7. Зависимости между агентами

### Жёсткие зависимости

- [ ] Sprint 2 зависит от Sprint 1
- [ ] Sprint 3 зависит от Sprint 1
- [ ] Sprint 3 желательно зависит от Sprint 2, если knowledge-base retrieval уже должен быть на Qdrant

### Что можно делать параллельно

- [ ] Внутри Sprint 1:
  - [ ] protocol contract
  - [ ] retyping call sites
- [ ] Внутри Sprint 2:
  - [ ] payload schema/store skeleton
  - [ ] filter/search contract
  - [ ] retrieval integration
- [ ] Внутри Sprint 3:
  - [ ] tool contracts
  - [ ] upload/document binding
  - [ ] Open WebUI integration docs/config

### Что нельзя делать параллельно

- [ ] Два агента не должны одновременно глубоко править:
  - [ ] [backend/orchestrator/knowledge_base_store.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_store.py)
  - [ ] [backend/orchestrator/knowledge_base_retrieval.py](/home/seral/HDD/proj/agent-navigator-pro/backend/orchestrator/knowledge_base_retrieval.py)
- [ ] Нельзя смешивать Sprint 2 и Sprint 3 в один PR

---

## 8. Рекомендуемый порядок выдачи задач агентам

### Wave 1

- [ ] Agent 1: `KnowledgeBaseStoreProtocol`
- [ ] Agent 2: retyping retrieval/ingestion + tests

### Wave 2

- [ ] Agent 1: `QdrantKnowledgeBaseStore` skeleton + payload schema
- [ ] Agent 2: Qdrant search/filter contract
- [ ] Agent 3: `knowledge_base_retrieval.py` integration + regression tests

### Wave 3

- [ ] Agent 1: tool contracts in `agent_api.py`
- [ ] Agent 2: upload/document binding
- [ ] Agent 3: Open WebUI tool-server integration

### Wave 4

- [ ] hardening / regression / hybrid follow-ups

---

## 9. Критерии успеха всего migration-track

- [ ] Backend tools стали primary execution boundary
- [ ] Retrieval/storage отделены от SQLite-specific кода
- [ ] Qdrant используется как knowledge-base vector backend
- [ ] Upload/document binding backend-owned
- [ ] Open WebUI работает поверх тех же backend contracts
- [ ] Переход выполнен без `big-bang rewrite`
