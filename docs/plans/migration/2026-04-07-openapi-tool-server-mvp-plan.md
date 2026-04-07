# OpenAPI Tool Server MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Подготовить минимальный backend-owned `OpenAPI Tool Server` contour для `Open WebUI`, не ломая текущий `Chainlit-first` runtime и не переписывая сразу весь document lifecycle.

**Architecture:** `Open WebUI` рассматривается как thin shell, а backend — как source-of-truth для tool execution, routing policy и long-running jobs. MVP вводит явный tool catalog, frontend-neutral orchestration contract и базовый job/result split, но не делает full file lifecycle rewrite и не уводит систему в `MCP-first`.

**Tech Stack:** FastAPI, Pydantic, existing orchestrator runtime, Open WebUI OpenAPI Tool Servers

---

## MVP Boundaries

### In

- явный catalog backend tools;
- `requested_tool` / `routing_mode` contract;
- минимальная граница между synchronous tool result и heavy job result;
- Open WebUI-facing OpenAPI surface;
- auth/CORS/host-mapping decisions, необходимые для первого интеграционного прохода.

### Out

- полный rewrite `OrchestrationRequest`;
- backend-owned upload/document binding implementation;
- ранний rename `backend/open_webui_uploads`;
- `MCP-first` integration;
- перенос предметной логики в `Open WebUI Functions`.

## Tool Catalog

MVP tool catalog:

- `ask_document`
- `analyze_document_fast`
- `analyze_document_deep`
- `compare_documents_fast`
- `compare_documents_deep`
- `analyze_equipment_fast`
- `analyze_equipment_deep`

## Contract Decisions

### 1. Routing contract

- Ввести публичный backend-owned contract:
  - `requested_tool: str | None`
  - `routing_mode: "explicit" | "assisted" | "auto"`
- Правило приоритета:
  1. `requested_tool`, если передан и валиден
  2. planner/classifier hint только при `routing_mode != explicit`
  3. fallback routing как последний путь
- `/v1/chat/completions` сохраняется как compatibility surface и не становится source-of-truth для tool MVP.

### 2. Tool result contract

Для quick tools:

- `status: "completed"`
- `tool_name`
- `assistant_message`
- `structured_result`
- `sources`
- `execution_metadata`

Для heavy tools:

- `status: "accepted"`
- `tool_name`
- `job_id`
- `status_url`
- `submitted_at`
- optional `result_preview`

### 3. Job contract

Минимальная модель job polling:

- `job_id`
- `status`
- `current_stage`
- `submitted_at`
- `started_at`
- `completed_at`
- `result_ref`
- `error_summary`

### 4. Open WebUI integration assumptions

- primary path: `OpenAPI Tool Server`
- tool server должен быть доступен отдельно от native `Open WebUI RAG`
- первая фаза не требует `Functions`
- первая фаза должна быть совместима как минимум с `User Tool Servers`; `Global Tool Servers` допускаются как admin-managed follow-up

### 5. Security / transport baseline

- отдельный auth/token contract между `Open WebUI` и backend tool server
- CORS не расширять сверх нужных origins при production-like path
- host/container mapping оставлять явным и документированным

## Execution Order

1. Зафиксировать tool registry и names как source-of-truth.
2. Спроектировать Pydantic request/response schemas для каждого tool.
3. Вынести frontend-neutral `requested_tool` / `routing_mode`.
4. Ввести quick-result vs accepted-job split.
5. Описать OpenAPI surface для `Open WebUI`.
6. Добавить targeted tests на routing priority и contract shape.
7. Только после этого подключать `Open WebUI` к tool server.

## Verification

- `python -m py_compile` по затронутым Python-модулям
- targeted `pytest` для:
  - routing priority
  - request/response schemas
  - heavy job acceptance contract
  - OpenAPI surface smoke
- `git diff --check`

## Notes

- Этот MVP intentionally не решает полный file/document lifecycle.
- Если в процессе выяснится, что один из tools требует backend-owned `document_id` до запуска MVP, это фиксируется как dependency и уходит в `M2.*` migration backlog, а не форсируется в этот slice.
