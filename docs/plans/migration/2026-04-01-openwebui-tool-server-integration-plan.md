# Open WebUI Tool Server Integration Plan

**Дата:** 2026-04-01
**Статус:** Proposed
**Связанные документы:**
- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)
- [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)
- [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)
- [MIGRATION_OVERIEW_WITH_LEAKS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_OVERIEW_WITH_LEAKS.md)
- [2026-04-01-knowledge-base-store-protocol-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-knowledge-base-store-protocol-plan.md)
- [2026-04-01-qdrant-knowledge-base-store-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md)

## 1. Цель

- [ ] Перевести систему к модели `Open WebUI -> FastAPI tool server -> backend workflows`.
- [ ] Сохранить текущие specialized tool-сценарии:
  - [ ] `analyze_document_fast/deep`
  - [ ] `compare_documents_fast/deep`
  - [ ] `analyze_equipment_fast/deep`
- [ ] Подготовить backend к knowledge-base migration:
  - [ ] `KnowledgeBaseStoreProtocol`
  - [ ] `QdrantKnowledgeBaseStore`
- [ ] Оставить `Chainlit` только как transitional compatibility/debug shell до sunset slice.
- [ ] Не дублировать через tool server обычный `Knowledge` chat path, который должен жить нативно в `Open WebUI`.

**Ключевое правило:**
Никакого `big-bang rewrite`.  
Переход идёт слоями: сначала ownership/docs freeze, потом storage/retrieval и ingestion boundaries, затем `Open WebUI Knowledge + Qdrant`, и только после этого фиксируется окончательная роль explicit backend tools.

---

## 2. Каноническая целевая схема

- [ ] Native knowledge path:
  - [ ] `Open WebUI`
  - [ ] `Knowledge / files / collections`
  - [ ] external parser-ingestion service
  - [ ] `Qdrant`
- [ ] Explicit tools path:
  - [ ] `Open WebUI`
  -> [ ] `Tool / OpenAPI client layer`
  -> [ ] `FastAPI backend tool server`
  -> [ ] `execution_runtime / orchestration_runtime`
  -> [ ] `workflow modules`
  -> [ ] `KnowledgeBaseStoreProtocol`
  -> [ ] `SQLiteKnowledgeBaseStore | QdrantKnowledgeBaseStore`
  -> [ ] `document server / legal server / UMS / vector backend`

### Нормативные принципы

- [ ] UI не знает деталей SQLite/Qdrant.
- [ ] UI не знает деталей `Chainlit` session-state.
- [ ] `agent_api.py` становится главным внешним tool-server entrypoint.
- [ ] Workflow execution не зависит от конкретного UI.
- [ ] Retrieval/storage boundaries оформляются до смены основного UI.
- [ ] Обычный question-answer по документу не должен насильно маршрутизироваться через tool server, если его должен закрывать native `Open WebUI Knowledge`.

---

## 3. Слой 1: Tool contracts

### Цель слоя

- [ ] Перестать считать `orchestrate` единственным универсальным входом.
- [ ] Ввести явные backend tools с узкими контрактами.
- [ ] Сделать их пригодными для `Open WebUI` Tool Server / OpenAPI integration.

### Обязательный минимальный набор tools

- [ ] `analyze_document_fast`
- [ ] `analyze_document_deep`
- [ ] `compare_documents_fast`
- [ ] `compare_documents_deep`
- [ ] `analyze_equipment_fast`
- [ ] `analyze_equipment_deep`

Уточнение:

- [ ] `ask_document` не считаем безусловным permanent baseline tool.
- [ ] Он допустим как compatibility bridge или thin adapter над тем же retrieval source-of-truth, что и native `Open WebUI Knowledge`.
- [ ] Основной UX для “задать вопрос по документу” должен определяться отдельно и не смешиваться с structured analysis tools.

### Что должно быть в contract каждого tool

- [ ] name
- [ ] input schema
- [ ] file/document requirements
- [ ] response schema
- [ ] execution metadata
- [ ] error contract
- [ ] optional streaming semantics

### Нормативное решение

- [ ] `Open WebUI` выбирает tool явно или через свою tool layer.
- [ ] Наш backend исполняет tool напрямую.
- [ ] Mandatory classifier/orchestrator не должен оставаться критическим шагом для manual-first сценария.
- [ ] Tool server path покрывает specialized workflows, а не обязан дублировать весь native Knowledge UX.

---

## 4. Слой 2: Backend execution boundary

### Цель слоя

- [ ] Отделить execution от UI-specific logic.
- [ ] Сохранить текущий `execution_runtime` как канонический backend execution core.
- [ ] Превратить `agent_api.py` в стабильный external tool server.

### Что должно остаться в backend core

- [ ] runtime context
- [ ] workflow execution
- [ ] evidence assembly
- [ ] telemetry
- [ ] validation / fallback / error handling

### Что нельзя тащить в Open WebUI

- [ ] `Chainlit`-specific state machine
- [ ] `cl.Step`
- [ ] UI-local restoration hacks
- [ ] `Chainlit`-specific settings contract

### Нормативное решение

- [ ] `Chainlit` и `Open WebUI` должны вызывать один и тот же backend execution слой.
- [ ] UI migration не должна менять доменную логику workflow.
- [ ] Но `Open WebUI Knowledge` может оставаться отдельным native chat/retrieval contour без обязательного backend tool call на каждый вопрос по документу.

---

## 5. Слой 3: Knowledge base abstraction

### Цель слоя

- [ ] Отделить retrieval/orchestration код от concrete SQLite storage.
- [ ] Ввести `KnowledgeBaseStoreProtocol` как backend storage contract.

### Что входит

- [ ] `KnowledgeBaseStoreProtocol`
- [ ] `get_knowledge_base_store()` как factory boundary
- [ ] перевод ingestion/retrieval type annotations на protocol

### Что это даёт

- [ ] SQLite остаётся working backend
- [ ] Qdrant можно добавить позже без переписывания call sites
- [ ] Open WebUI migration не зависит от knowledge store implementation details
- [ ] Native `Open WebUI Knowledge` и backend workflows могут опираться на один и тот же corpus/vector source-of-truth

### Нормативное решение

- [ ] Этот слой делается до Qdrant и до основного UI switch.

---

## 6. Слой 4: Qdrant retrieval backend

### Цель слоя

- [ ] Вынести KB retrieval из модели “читать всё и строить transient retriever локально”.
- [ ] Перейти к vector DB с payload filters.

### Что должно появиться

- [ ] `QdrantKnowledgeBaseStore`
- [ ] payload schema
- [ ] stable point IDs
- [ ] query-time filtering
- [ ] vector search primitive для KB path

### Что должно сохраниться

- [ ] `knowledge_base_retrieval.py` как policy/merge layer
- [ ] `session_rag`
- [ ] `knowledge_base_rag`
- [ ] `knowledge_base + session overlay`
- [ ] provenance / rerank / dedup

### Recommended rollout

- [ ] Phase 1: `dense-first`
- [ ] Phase 2: optional lexical correction / shortlist BM25
- [ ] Phase 3: full hybrid backend if needed

### Нормативное решение

- [ ] Qdrant внедряется как storage/retrieval backend, а не как новая orchestration system.

---

## 7. Слой 5: Upload и document binding

### Цель слоя

- [ ] Сделать совместимую модель загрузки файлов и привязки документов к chat/thread.
- [ ] Подготовить backend как tool server для `Open WebUI`, который отправляет файлы не как локальные пути, а через upload flow.
- [ ] Не считать built-in parsing inside `Open WebUI` production source-of-truth для сложных PDF/OCR/table extraction.

### Что должно быть

- [ ] стабильный upload endpoint / upload contract
- [ ] storage path для uploaded files
- [ ] document registration
- [ ] `document_id`
- [ ] `version_id`
- [ ] binding `document <-> thread/chat/workspace`
- [ ] attach/detach/activate/deactivate semantics

### Что должно поддерживаться

- [ ] freshly uploaded docs
- [ ] session overlay
- [ ] later KB ingestion
- [ ] long-lived knowledge base bindings
- [ ] handoff во внешний parser-ingestion contour

### Нормативное решение

- [ ] Upload не должен зависеть от `Chainlit` file handling.
- [ ] Open WebUI path должен работать через backend-owned document binding model.
- [ ] Для production parsing нужен отдельный contour (`Docling` first, `Tika` fallback), даже если `Open WebUI` умеет basic file processing.

---

## 8. Слой 6: Retrieval scopes

### Цель слоя

- [ ] Сохранить текущие retrieval modes в новом UI/Tool Server пути.

### Required scopes

- [ ] `session_rag`
- [ ] `knowledge_base_rag`
- [ ] `knowledge_base + session overlay`

### Как это должно работать

- [ ] session docs:
  - [ ] локальные временные документы текущего чата
- [ ] knowledge base:
  - [ ] persisted indexed chunks из KB backend
- [ ] overlay:
  - [ ] merged shortlist из persisted KB + freshly attached docs

### Нормативное решение

- [ ] Scope policy живёт в backend, а не в Open WebUI prompt hacks.

### Практическое UX-разделение

- [ ] Native `Open WebUI Knowledge`:
  - [ ] обычный document chat;
  - [ ] retrieval по KB;
  - [ ] follow-up вопросы по уже загруженному корпусу.
- [ ] Explicit backend tools:
  - [ ] structured analysis;
  - [ ] compare/report;
  - [ ] equipment-specific flows;
  - [ ] async jobs / artifacts / operator-visible lifecycle.

---

## 9. Слой 7: OpenAI-compatible / Tool Server compatibility

### Цель слоя

- [ ] Дать `Open WebUI` совместимый способ вызова backend.

### Варианты

- [ ] OpenAPI Tool Server path
- [ ] OpenAI-compatible path
- [ ] hybrid compatibility layer during migration

### Что имеет смысл внедрять

- [ ] сначала явные tool endpoints
- [ ] потом, если реально нужно:
  - [ ] `/v1/files`
  - [ ] strict SSE compatibility
  - [ ] `/v1/models` completeness

### Нормативное решение

- [ ] Tool endpoints приоритетнее, чем попытка всё решить через legacy `/v1/chat/completions`.

---

## 10. Слой 8: Переходный режим `Chainlit -> Open WebUI`

### Цель слоя

- [ ] Перевести основной UX на `Open WebUI`, не ломая текущий `Chainlit`.

### Transitional model

- [ ] `Chainlit` остаётся рабочим клиентом на переходный период
- [ ] `Open WebUI` подключается к тем же backend tools
- [ ] оба UI работают поверх общего backend execution/retrieval contract

### Когда можно считать миграцию состоявшейся

- [ ] все обязательные user flows доступны в `Open WebUI`
- [ ] upload/document binding работает
- [ ] retrieval scopes не деградировали
- [ ] telemetry / errors / evidence contract не хуже текущего уровня

### Когда можно переводить `Chainlit` в legacy

- [ ] после прохождения regression и UX validation
- [ ] не раньше, чем Open WebUI path покроет:
  - [ ] upload
  - [ ] manual tool invocation
  - [ ] grounded doc QA
  - [ ] compare
  - [ ] equipment flow

---

## 11. Поэтапный маршрут реализации

## Phase A: Tool contracts

- [ ] оформить отдельные backend tool endpoints
- [ ] сузить request/response schemas
- [ ] отделить tools от legacy `orchestrate`

## Phase B: Store boundary

- [ ] внедрить `KnowledgeBaseStoreProtocol`
- [ ] отвязать retrieval/ingestion от SQLite class

## Phase C: Qdrant backend

- [ ] внедрить `QdrantKnowledgeBaseStore`
- [ ] payload schema
- [ ] filtering
- [ ] vector shortlist retrieval

## Phase D: Upload/document binding

- [ ] backend-owned upload model
- [ ] document/version/binding entities
- [ ] session overlay path

## Phase E: Open WebUI integration

- [ ] подключить Open WebUI к backend tools
- [ ] проверить manual-first flows
- [ ] не убирать `Chainlit` до прохождения regression

## Phase F: Compatibility cleanup

- [ ] решить, что остаётся от OpenAI-compatible path
- [ ] перевести `Chainlit` в transitional/legacy mode

---

## 12. Что нельзя делать

- [ ] Не менять одновременно:
  - [ ] UI
  - [ ] storage backend
  - [ ] retrieval policy
  - [ ] workflow contracts
  в одном PR/slice
- [ ] Не тащить `Chainlit`-specific state в новый Open WebUI path
- [ ] Не делать Qdrant migration до storage contract boundary
- [ ] Не делать Open WebUI основным UI до готовности upload/document binding

---

## 13. Итоговое нормативное решение

- [ ] Система строится слоями, а не одним переносом.
- [ ] `Tool contracts` — первый слой.
- [ ] `KnowledgeBaseStoreProtocol` — обязательный архитектурный мост.
- [ ] `QdrantKnowledgeBaseStore` — следующий слой backend retrieval/storage.
- [ ] `upload/document binding` — обязательный слой перед полноценным Open WebUI UX.
- [ ] `Chainlit -> Open WebUI` делается только поверх уже стабилизированных backend boundaries.
