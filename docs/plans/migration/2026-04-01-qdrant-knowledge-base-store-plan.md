# Qdrant Knowledge Base Store Plan

**Дата:** 2026-04-01  
**Статус:** Proposed  
**Связанные документы:**  
- [TASKS.md](/home/seral/HDD/proj/agent-navigator-pro/TASKS.md)  
- [MIGRATION_TOOLS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_TOOLS.md)  
- [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md)  
- [MIGRATION_OVERIEW_WITH_LEAKS.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_OVERIEW_WITH_LEAKS.md)  
- [2026-03-13-b333-knowledge-base-rag.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/2026-03-13-b333-knowledge-base-rag.md)  
- [2026-04-01-knowledge-base-store-protocol-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-knowledge-base-store-protocol-plan.md)

## 1. Назначение плана

- [ ] Спроектировать отдельный backend slice для `QdrantKnowledgeBaseStore`.
- [ ] Перевести knowledge-base retrieval с transient SQLite-backed chunk scan к vector DB backend с payload filters.
- [ ] Сохранить текущий orchestration/retrieval contract:
  - [ ] `session_rag`
  - [ ] `knowledge_base_rag`
  - [ ] `knowledge_base + session overlay`
- [ ] Не смешивать этот слой с UI rewrite, но проектировать его как canonical backend для `Open WebUI Knowledge`.
- [ ] Зафиксировать, что production parsing/ingestion идёт через внешний parser-service, а не только через встроенный UI path.

**Ключевая идея:**  
`QdrantKnowledgeBaseStore` должен стать не параллельной альтернативной логикой, а backend implementation за уже выделенным `KnowledgeBaseStoreProtocol`, с сохранением текущего retrieval/execution контракта для вызывающего кода. При этом он рассматривается как canonical vector backend для `Open WebUI Knowledge` и backend knowledge-base paths, а не как изолированный backend-only эксперимент.

---

## 2. Архитектурная роль Qdrant в проекте

### 2.1 Что должно измениться

- [ ] Уйти от модели:
  - [ ] “прочитать все KB chunks коллекции”
  - [ ] “локально собрать transient retriever”
  - [ ] “искать только в памяти текущего процесса”
- [ ] Перейти к модели:
  - [ ] persisted vector index;
  - [ ] query-time filtering по payload;
  - [ ] retrieval как backend service boundary, а не как rebuild локального индекса.
- [ ] Обеспечить один canonical retrieval source-of-truth для `Open WebUI Knowledge` и backend workflows, даже если UX-path у них различается.

### 2.2 Что должно остаться

- [ ] `execution_runtime` не должен знать деталей Qdrant API.
- [ ] `knowledge_base_retrieval.py` должен сохранить:
  - [ ] merged retrieval semantics;
  - [ ] provenance;
  - [ ] session overlay;
  - [ ] rerank hook;
  - [ ] `source_scope_summary`.
- [ ] UI-слой не должен знать про SQLite/Qdrant.

### 2.3 Что этот план говорит про `Open WebUI`

- [ ] `Open WebUI` может быть primary UI и owner user-facing Knowledge UX.
- [ ] Но `Open WebUI` не должен становиться единственным местом, где живут canonical corpus metadata, parsing policy и ingestion lifecycle.
- [ ] Для production contour нужно разделять:
  - [ ] UI/Knowledge UX
  - [ ] parser-ingestion services
  - [ ] vector backend (`Qdrant`)
  - [ ] corpus admin / metadata / audit surface

---

## 3. Текущие ограничения текущей реализации

### 3.1 Current `knowledge_base_retrieval.py`

- [ ] Сейчас `_build_kb_entries(...)` читает **все** чанки коллекции через `list_chunks_sync(..., include_embeddings=True)`.
- [ ] Потом `_search_entries(...)` строит `HybridRetriever` в памяти процесса.
- [ ] Если embeddings уже есть, используется:
  - [ ] `HybridRetriever.index_with_embeddings(...)`
- [ ] Иначе идёт локальный `index(...)` с `embed_fn`.

### 3.2 Почему это уже недостаточно

- [ ] Плохо масштабируется по размеру коллекции.
- [ ] Увеличивает latency на каждый запрос.
- [ ] Не соответствует целевой модели из [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md):
  - [ ] retrieval по filters;
  - [ ] vector DB + metadata DB;
  - [ ] self-hosted product architecture.

### 3.3 Что нельзя потерять

- [ ] Session overlay для свежих документов.
- [ ] Deterministic fallback behavior.
- [ ] Возможность BM25/hybrid path, а не только dense-only search.
- [ ] Existing source provenance и candidate merge policy.

---

## 4. Целевая архитектура

### 4.1 Каноническая схема

- [ ] `Open WebUI / Chainlit / API client`
  -> [ ] `FastAPI backend`
  -> [ ] `execution_runtime / orchestration_runtime`
  -> [ ] `knowledge_base_retrieval.py`
  -> [ ] `KnowledgeBaseStoreProtocol`
  -> [ ] `QdrantKnowledgeBaseStore`
  -> [ ] `Qdrant collection + payload filters`

Практическое уточнение:

- [ ] `Open WebUI` остаётся primary shell;
- [ ] `Chainlit` в этом контуре считается compatibility/debug client до sunset;
- [ ] external parser-service (`Docling` first, `Tika` fallback) подготавливает documents/chunks для последующей индексации в `Qdrant`.

### 4.2 Разделение ответственности

- [ ] `QdrantKnowledgeBaseStore`
  - [ ] persistence;
  - [ ] vector write/update/delete;
  - [ ] payload-level filtering;
  - [ ] retrieval query primitive;
  - [ ] чтение chunk/source entities обратно в канонические dataclasses.
- [ ] `knowledge_base_retrieval.py`
  - [ ] решает scope policy;
  - [ ] объединяет KB + session overlay;
  - [ ] делает dedup;
  - [ ] строит `source_scope_summary`;
  - [ ] optionally rerank top-N.
- [ ] `execution_runtime.py`
  - [ ] остаётся вызывающей orchestration boundary и не знает внутренностей vector DB.

### 4.3 V1 / V2 граница для session docs

- [ ] V1:
  - [ ] persistent knowledge base хранится в `Qdrant`;
  - [ ] session overlay может оставаться вне `Qdrant`;
  - [ ] merge policy остаётся в `knowledge_base_retrieval.py`.
- [ ] V2:
  - [ ] short-lived session docs можно хранить в `Qdrant`;
  - [ ] filtering идёт по `thread_id` / `workspace_id` / `source_scope`;
  - [ ] TTL/cleanup и visibility policy остаются отдельным backend/infra contract.

---

## 5. Payload schema для Qdrant

### 5.1 Обязательные payload-поля

- [ ] `tenant_id`
- [ ] `collection_id`
- [ ] `source_id`
- [ ] `document_id`
- [ ] `version_id`
- [ ] `display_name`
- [ ] `chunk_id`
- [ ] `chunk_index`
- [ ] `source_origin`
- [ ] `text`
- [ ] `embedding_model_id`
- [ ] `index_version`
- [ ] `chunking_version`
- [ ] `mime_type`
- [ ] `doc_type`
- [ ] `language`
- [ ] `thread_id`
- [ ] `workspace_id`
- [ ] `source_scope`
- [ ] `section_title`
- [ ] `page`
- [ ] `tags`
- [ ] `content_hash`
- [ ] `created_at`
- [ ] `updated_at`

### 5.2 Минимально достаточный payload для V1

Если весь список слишком широк для первого шага, V1 должен включать минимум:

- [ ] `collection_id`
- [ ] `source_id`
- [ ] `display_name`
- [ ] `chunk_id`
- [ ] `chunk_index`
- [ ] `source_origin`
- [ ] `text`
- [ ] `content_hash`
- [ ] `embedding_model_id`
- [ ] `index_version`
- [ ] `chunking_version`
- [ ] `doc_type`
- [ ] `language`
- [ ] `section_title`

### 5.3 Нормативные требования к payload

- [ ] Payload должен быть фильтруемым без дополнительных round-trips к отдельной SQLite-таблице для базового retrieval.
- [ ] `text` должен оставаться доступным напрямую для формирования evidence blocks.
- [ ] `content_hash` должен использоваться для idempotent ingestion и dedup policy.
- [ ] Payload schema должна позволять future filters:
  - [ ] by tenant
  - [ ] by thread/workspace
  - [ ] by document/version
  - [ ] by source scope
  - [ ] by doc type / tags / language

---

## 6. Схема коллекции и IDs

### 6.1 Point identity

- [ ] Один chunk = один point в Qdrant
- [ ] `point_id` должен быть стабильным и детерминированным
- [ ] Рекомендуемый кандидат:
  - [ ] hash от `collection_id + source_id + chunk_index + content_hash`
  - [ ] либо UUIDv5 от тех же полей

### 6.2 Collection strategy

- [ ] Не создавать отдельную коллекцию на каждый документ.
- [ ] Предпочесть одну логическую collection на knowledge-base domain / tenant scope.
- [ ] Фильтрация должна идти по payload, а не по explosion количества коллекций.

### 6.3 Multi-tenant / workspace strategy

- [ ] Если multitenancy реально потребуется:
  - [ ] tenant/workspace должны идти в payload filters;
  - [ ] не кодировать tenancy только в имени коллекции.

---

## 7. Dense retrieval strategy

### 7.1 Базовый режим

- [ ] Qdrant должен использоваться как primary dense retrieval backend.
- [ ] Вектор пишется при ingestion.
- [ ] Query embedding создаётся retrieval-слоем через текущий `embed_fn`.

### 7.2 Query flow

- [ ] `knowledge_base_retrieval.py` получает query
- [ ] вызывает `embed_fn([query])`
- [ ] передаёт query vector в `QdrantKnowledgeBaseStore.search_chunks(...)`
- [ ] store возвращает shortlist кандидатов с:
  - [ ] `KnowledgeBaseChunkRecord`
  - [ ] raw vector similarity score
  - [ ] payload metadata

### 7.3 Что нельзя делать

- [ ] Не строить локальный dense index поверх уже выгруженных из Qdrant embeddings для всего KB.
- [ ] Не вытаскивать все чанки коллекции в память на каждый запрос.

---

## 8. Hybrid retrieval strategy

### 8.1 Требование

- [ ] Не терять hybrid retrieval как product capability.
- [ ] Но не пытаться решить весь sparse/hybrid rollout в одной фазе.

### 8.2 Recommended phased approach

#### Phase H1

- [ ] Ввести Qdrant как dense retrieval backend.
- [ ] Session overlay продолжает использовать текущий local `HybridRetriever`.
- [ ] KB path initially работает как dense-first shortlist.

#### Phase H2

- [ ] Добавить optional local BM25 по shortlist candidate texts, уже возвращённым из Qdrant.
- [ ] Использовать этот слой как lightweight lexical correction, а не как полный второй индекс для всей KB.

#### Phase H3

- [ ] При необходимости внедрить полноценный sparse/hybrid backend path:
  - [ ] Qdrant sparse vectors / named vectors
  - [ ] или отдельный sparse index service
  - [ ] или hybrid fusion поверх двух retrieval providers

### 8.3 Нормативное решение для этого плана

- [ ] В этом плане проектировать Qdrant rollout как **dense-first with hybrid-compatible boundary**
- [ ] Не блокировать миграцию тем, что full sparse rollout ещё не готов

---

## 9. Filtering strategy

### 9.1 Required filters

- [ ] `collection_id`
- [ ] `source_id`
- [ ] `document_id`
- [ ] `version_id`
- [ ] `source_origin`
- [ ] `thread_id`
- [ ] `workspace_id`
- [ ] `doc_type`
- [ ] `language`
- [ ] `tags`

### 9.2 Required filter combinations

- [ ] Knowledge base only:
  - [ ] `collection_id = X`
- [ ] Single document / bound docs:
  - [ ] `collection_id = X`
  - [ ] `document_id in [...]`
- [ ] Tenant / workspace scoped:
  - [ ] `tenant_id = X`
  - [ ] `workspace_id = Y`
- [ ] Thread overlay-aware future mode:
  - [ ] `thread_id = Z`
  - [ ] with optional `source_scope = overlay`

### 9.3 Why filtering matters

- [ ] Это главный мост к [MIGRATION_RAG_OPENWEBUI.md](/home/seral/HDD/proj/agent-navigator-pro/MIGRATION_RAG_OPENWEBUI.md), где retrieval должен происходить по scope filters, а не через rebuild local index.

---

## 10. Стыковка с текущим `knowledge_base_retrieval.py`

### 10.1 Что нужно сохранить

- [ ] `_build_session_entries(...)`
- [ ] `_annotate_scope_merge_scores(...)`
- [ ] `_dedup_and_normalize(...)`
- [ ] `_apply_optional_rerank(...)`
- [ ] `source_scope_summary`

### 10.2 Что нужно заменить

- [ ] `_build_kb_entries(...)` в текущем виде не должен читать всю коллекцию через `list_chunks_sync(..., include_embeddings=True)`.
- [ ] Вместо этого нужен store-level query method, например:
  - [ ] `search_chunks_sync(...)`
  - [ ] `search_chunks(...)`

### 10.3 Новый retrieval flow для KB path

- [ ] `knowledge_base_retrieval.py` формирует query vector и filter intent
- [ ] вызывает `kb_store.search_chunks(...)`
- [ ] получает shortlist KB кандидатов
- [ ] отдельно строит session entries локально
- [ ] при наличии session overlay:
  - [ ] session path может продолжать использовать local `HybridRetriever`
- [ ] KB shortlist + session shortlist объединяются в существующем merge layer

### 10.4 Нормативное правило

- [ ] `knowledge_base_retrieval.py` остаётся policy/merge layer
- [ ] vector DB querying не должен расползтись прямо в `execution_runtime.py`

---

## 11. Target API surface for `QdrantKnowledgeBaseStore`

### 11.1 Ingestion/write side

- [ ] `register_source_sync(...)`
- [ ] `replace_chunks_sync(...)`
- [ ] `delete_source_sync(...)` if needed later

### 11.2 Read side

- [ ] `list_sources_sync(...)`
- [ ] `list_chunks_sync(...)`

### 11.3 Vector search side

- [ ] Добавить отдельный метод в protocol через follow-up extension:
  - [ ] `search_chunks_sync(...)`
  - [ ] `search_chunks(...)`

### 11.4 Important note

- [ ] Если `KnowledgeBaseStoreProtocol` V1 пока не содержит `search_chunks`, то Qdrant rollout должен идти в две подфазы:
  - [ ] сначала protocol for CRUD/list
  - [ ] потом protocol extension for vector search

---

## 12. Migration phases

## Phase Q1: Finalize protocol boundary

- [ ] Завершить `KnowledgeBaseStoreProtocol` slice
- [ ] Убедиться, что retrieval/ingestion больше не зависят от SQLite class typing

## Phase Q2: Introduce Qdrant backend implementation

- [ ] Добавить `QdrantKnowledgeBaseStore`
- [ ] Реализовать write/update/list contract
- [ ] Реализовать payload schema
- [ ] Поддержать idempotent replace for chunks per source/version

## Phase Q3: Add vector search contract

- [ ] Ввести `search_chunks(...)` в store boundary
- [ ] Подключить его в `knowledge_base_retrieval.py`
- [ ] Убрать full collection scan для KB retrieval

## Phase Q4: Merge with session overlay

- [ ] Сохранить local session overlay path
- [ ] Объединять Qdrant shortlist и session shortlist в текущем merge layer
- [ ] Проверить provenance/dedup/source scope semantics

## Phase Q5: Optional hybrid hardening

- [ ] Decide on sparse/hybrid strategy
- [ ] Ввести rerank/fusion hardening только после стабилизации dense-first path

---

## 13. Test plan

### 13.1 Store contract tests

- [ ] `QdrantKnowledgeBaseStore` satisfies `KnowledgeBaseStoreProtocol`
- [ ] source registration/update works
- [ ] replace_chunks is idempotent
- [ ] payload schema is stable

### 13.2 Retrieval tests

- [ ] `knowledge_base_rag` returns KB hits from Qdrant backend
- [ ] `knowledge_base_rag + session overlay` still merges correctly
- [ ] `source_scope_summary` remains correct
- [ ] dedup by normalized text / content hash still works

### 13.3 Filtering tests

- [ ] collection filter
- [ ] document filter
- [ ] tenant/workspace filter
- [ ] doc_type/language filter

### 13.4 Regression tests

- [ ] existing KB retrieval tests adapted
- [ ] `execution_runtime` doc-QA path remains green
- [ ] no regression for `session_rag` path

---

## 14. Risks and non-goals

### Risks

- [ ] Слишком рано перенести весь retrieval в vector DB и потерять current merge semantics
- [ ] Смешать metadata DB concerns и vector payload concerns
- [ ] Попытаться решить full sparse/hybrid system в том же slice
- [ ] Утянуть Qdrant internals в orchestration layer

### Non-goals

- [ ] Не делать full Open WebUI migration в этом плане
- [ ] Не делать PostgreSQL metadata layer в этом же slice
- [ ] Не заменять сразу все session overlay paths на persisted storage
- [ ] Не переписывать `HybridRetriever` целиком

---

## 15. Итоговое нормативное решение

- [ ] `QdrantKnowledgeBaseStore` должен внедряться как backend implementation за protocol boundary
- [ ] Payload schema и filters являются core design surface этого слоя
- [ ] Dense-first rollout допустим, если сохраняется hybrid-compatible boundary
- [ ] `knowledge_base_retrieval.py` должен остаться policy/merge layer, а не превратиться в слой прямых Qdrant деталей
- [ ] Open WebUI migration должна использовать уже стабилизированный backend retrieval contract, а не совмещаться с первичным проектированием vector storage
