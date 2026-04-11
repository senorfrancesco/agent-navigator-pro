# Open WebUI-First RAG Architecture Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Зафиксировать каноническую архитектуру `Open WebUI-first` для чата, Knowledge и explicit tools, чтобы migration-планы больше не смешивали UI, retrieval, ingestion и domain workflows.

**Architecture:** `Open WebUI` становится основным пользовательским shell для чата, истории, Knowledge и подключения инструментов. Подготовка документов выносится во внешний ingestion/parsing contour (`Docling` first, `Tika` fallback), `Qdrant` становится canonical vector backend для Knowledge, а backend сохраняет source-of-truth для explicit domain tools, async jobs, artifacts и corpus metadata/admin flows.

**Tech Stack:** Open WebUI, OpenAPI Tool Server, FastAPI, Qdrant, Docling, Apache Tika, UMS, document server

---

## Нормативное решение

- `Open WebUI` — целевой primary UI и верхний orchestration shell.
- `Chainlit` — только compatibility/debug shell до отдельного sunset slice.
- Для обычного вопроса по документу canonical path = `Open WebUI Knowledge`, а не backend tool path по умолчанию.
- `Qdrant` — canonical vector backend для Knowledge/RAG.
- Подготовка документов не живёт внутри UI: parsing/OCR/table extraction идут через внешний ingestion service.
- Explicit backend tools остаются для специализированных сценариев:
  - `analyze_document_fast/deep`
  - `compare_documents_fast/deep`
  - `analyze_equipment_fast/deep`
- `ask_document` не должен оставаться обязательным user-facing путём для обычного document chat:
  - либо становится thin adapter поверх того же retrieval source-of-truth, что и `Open WebUI Knowledge`;
  - либо понижается до compatibility tool и уходит из основного UX.

## Runtime Contours

### Contour A — Native Knowledge Chat

**Назначение:** обычный chat/Q&A по документам и knowledge base.

**Контур:**
- `Open WebUI`
- Knowledge / files / collections
- внешний parser-ingestion service
- embeddings service
- `Qdrant`

**Правила:**
- UI может нативно отвечать по документам через свой Knowledge contour;
- backend tool routing здесь не должен быть обязательным скрытым шагом;
- retrieval source-of-truth должен быть общим и проверяемым на уровне corpus/collections, а не только chat-session памяти UI.

### Contour B — Explicit Domain Tools

**Назначение:** структурированный анализ, сравнение, отчёты, equipment-specific flows.

**Контур:**
- `Open WebUI`
- named tools / action functions / prompts
- `OpenAPI Tool Server`
- `FastAPI backend`
- workflow modules / job store / artifacts

**Правила:**
- backend исполняет уже явно выбранный tool;
- domain prompts, async lifecycle, artifacts, telemetry и status polling живут в backend;
- `Open WebUI` показывает компактный result, follow-up actions и ссылки на job/report, а не сырой внутренний pipeline.

## V1 / V2 Boundary for Qdrant

### V1

- persistent knowledge-base chunks хранятся в `Qdrant`;
- session/fresh uploads могут жить как session overlay вне `Qdrant`;
- merge policy остаётся в backend retrieval layer;
- `Open WebUI Knowledge` работает поверх готового ingestion/indexing path.

### V2

- session docs можно переносить в `Qdrant` как short-lived scopes;
- filtering идёт по `thread_id` / `workspace_id` / `collection_id`;
- lifecycle очистки и TTL остаётся отдельной backend/infra задачей.

## Implementation Tasks

### Task 1: Doc Freeze on Open WebUI-First Direction

**Files:**
- Create: `docs/plans/migration/2026-04-10-openwebui-first-rag-architecture-plan.md`
- Modify: `docs/plans/migration/2026-04-02-unified-openwebui-migration-master-plan.md`
- Modify: `docs/plans/migration/2026-04-09-openwebui-responsibility-split-plan.md`

**Нужно зафиксировать:**
- `Open WebUI` — primary UI;
- `Chainlit` — sunset candidate;
- `Knowledge` и explicit tools — разные contours;
- `ask_document` больше не трактуется как единственный обязательный путь document QA.

### Task 2: External Ingestion Boundary

**Files:**
- Modify: `docs/plans/migration/2026-04-02-unified-openwebui-migration-master-plan.md`
- Modify: `docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md`

**Нужно зафиксировать:**
- `Docling` first, `Tika` fallback;
- parsing/OCR/tables выносятся из UI;
- corpus preparation и corpus admin не живут внутри `Open WebUI` state.

### Task 3: Qdrant Scope Clarification

**Files:**
- Modify: `docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md`
- Modify: `docs/plans/migration/2026-04-01-openwebui-tool-server-integration-plan.md`

**Нужно зафиксировать:**
- `Qdrant` — canonical backend для Knowledge;
- retrieval policy/merge layer остаётся у проекта;
- tool server не дублирует обычный knowledge chat, а покрывает explicit domain workflows.

### Task 4: Tool Catalog Re-Segmentation

**Files:**
- Modify: `docs/plans/migration/2026-04-01-openwebui-tool-server-integration-plan.md`
- Modify: `docs/plans/migration/2026-04-09-openwebui-responsibility-split-plan.md`

**Нужно зафиксировать:**
- `analyze_*`, `compare_*`, `equipment_*` остаются explicit tools;
- `ask_document` становится compatibility/thin-adapter decision, а не бесспорным baseline;
- acceptance для `Open WebUI` smoke должна различать native Knowledge path и backend tool path.

## Acceptance Criteria

- migration docs не конфликтуют друг с другом по вопросу `Open WebUI-first` vs `Chainlit-first`;
- `Qdrant` описан как canonical Knowledge backend, а не как побочный UI эксперимент;
- внешний ingestion contour формально введён в архитектуру;
- explicit tools и native Knowledge chat разведены по назначению и ownership;
- для implementer больше нет двусмысленности, зачем нужен backend tool path после включения `Open WebUI Knowledge`.
