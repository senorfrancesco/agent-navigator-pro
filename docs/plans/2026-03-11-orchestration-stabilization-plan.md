# Orchestration Stabilization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Убрать размытие ответственности между Chainlit UI и backend-оркестратором, стабилизировать runtime-контур и только после этого улучшать routing quality и hardware-adaptive behavior.

**Architecture:** Текущее репо уже имеет рабочий контур через `Chainlit + Agent API + LangGraph + AdaptiveRAGPipeline`, но часть routing/policy/state-логики живёт прямо в UI. План фиксирует последовательность: сначала единый orchestration contract и runtime mode switch, затем улучшение classifier и context-budgeting, и только потом UX-надстройки.

**Tech Stack:** Python 3.11, FastAPI, Chainlit, LangGraph, AdaptiveRAGPipeline, UMS, pytest.

---

> **Status Note:** Этот документ сохраняется как узкий stabilization-план для `B3.31/T4.13/T4.3`,
> но по состоянию на `2026-03-12` он уже является подмножеством более новых документов:
> [2026-03-11-unified-recovery-and-ui-plan.md](./2026-03-11-unified-recovery-and-ui-plan.md) и
> [2026-03-12-execution-review-corrective-plan.md](./2026-03-12-execution-review-corrective-plan.md).
> Для final execution order, `rag_scope`, `knowledge_base_rag` и merged retrieval
> использовать именно их как канон.

## Контекст

### Что уже есть в репо

- Архитектурная схема уже декларирует разделение UI и backend в [README.md](../../README.md).
- `agent_api` уже позиционируется как оркестратор в [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py).
- Фактическая routing-логика и mode/state-management всё ещё живут в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py).

### Главная проблема

Сейчас не отсутствует оркестратор как таковой. Проблема в том, что orchestration split-brain уже возник:

- backend объявлен как decision layer;
- UI по факту тоже принимает route/policy решения;
- из-за этого система ощущается нестабильной и “костыльной”, даже когда отдельные компоненты уже работают.

### Опорные backlog-задачи

1. [B3.31 — API Orchestration Layer + Runtime Mode Switch](../../TASKS.md#L510)
2. [B3.32 — LangChain adoption strategy](../../TASKS.md#L534)
3. [B3.21 — Quality upgrade: отдельная embedding-модель для intent classification](../../TASKS.md#L555)
4. [T4.13 — Runtime Context Budget + Preflight Profiles](../../TASKS.md#L671)
5. [T4.3 — LLM Profile Selector в Chainlit](../../TASKS.md#L615)

## Антикризисные принципы на 2 недели

1. Не добавлять новые workflow и новые “агенты”, пока не закрыт orchestration split.
2. Не делать full rewrite на LangChain.
3. Не улучшать classifier до стабилизации orchestration contract.
4. Не лечить слабое железо случайными лимитами в prompt/runtime там, где нужен системный context budget.
5. Любой UI control должен быть thin client над backend policy, а не вторым оркестратором.

## Фаза 1. Завершить разделение ответственности UI и backend

**Приоритет:** максимальный  
**Backlog:** [B3.31](../../TASKS.md#L510)

### Почему это первый шаг

Сейчас `Chainlit` сам делает то, что по backlog должен делать orchestrator:

- определяет intent в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- рассчитывает route-choice и ambiguity handling в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- исполняет локальный dispatch через `_execute_intent` в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)

Ключевые места:

- [backend/orchestrator/chainlit_app.py:508](../../backend/orchestrator/chainlit_app.py#L508)
- [backend/orchestrator/chainlit_app.py:596](../../backend/orchestrator/chainlit_app.py#L596)
- [backend/orchestrator/chainlit_app.py:702](../../backend/orchestrator/chainlit_app.py#L702)
- [backend/orchestrator/chainlit_app.py:1690](../../backend/orchestrator/chainlit_app.py#L1690)
- [backend/orchestrator/chainlit_app.py:1763](../../backend/orchestrator/chainlit_app.py#L1763)

### Цель фазы

Сделать backend единственным местом, где принимаются решения:

- `mode`
- `route`
- `executor`
- `model_profile`
- `rag_scope`
- `knowledge_collection_id`
- `source_scope_summary`
- `fallback`
- `action_required`
- `confidence`
- `trace_id`
- `state_ref`
- `pending_action_id`

### Отдельное уточнение по persistence ownership

Для текущего stabilization-step допустим intentional pragmatic compromise:

- `Chainlit SQLite` / existing Chainlit data layer временно остаётся authoritative
  для `resume`, `pending action`, `state_ref` и связанного recovery-state;
- это не считается конечной архитектурой ownership;
- dedicated backend-owned state store вынесен в отдельную future phase
  [B3.31a](../../TASKS.md).

Целевая модель после `B3.31a`:

- backend persistence/checkpointer является source of truth;
- `Chainlit` session state — только UI-mirror/cache;
- восстановление pending action и resumable execution идёт из backend state по `state_ref`,
  а не из UI-only эвристик.

### Что менять

- Модифицировать [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- Рефакторить [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- Добавить или расширить тесты в:
  - [backend/tests/test_document_analysis.py](../../backend/tests/test_document_analysis.py)
  - [backend/tests/test_chainlit_streaming.py](../../backend/tests/test_chainlit_streaming.py)
  - [backend/tests/test_unified_model_server_streaming.py](../../backend/tests/test_unified_model_server_streaming.py)
  - [backend/tests/test_unified_model_server_startup.py](../../backend/tests/test_unified_model_server_startup.py)

### Definition of Done

- `Chainlit` не решает локально, какой workflow запускать.
- `Chainlit` только рендерит backend-response и action prompts.
- В UI не остаётся локальных веток бизнес-роутинга.
- Один и тот же запрос в одном runtime-mode приводит к одному и тому же backend-route.
- `runtime_mode` не смешан с retrieval scope; `rag_scope` передаётся и хранится отдельно.
- для текущей фазы `resume/action_required` стабилизированы на pragmatic Chainlit-authoritative слое,
  а backend-owned persistence вынесен в отдельный follow-up `B3.31a`.

## Фаза 2. Ввести явные runtime modes

**Приоритет:** очень высокий  
**Backlog:** [B3.31](../../TASKS.md#L510)

### Цель фазы

Добавить режимы выполнения:

- `auto`
- `chat_only`
- `specialized_tasks`

### Зачем это нужно

Пользовательский переключатель “общий чат / агентная система” не должен быть UI-хаком. Он должен быть thin control над runtime policy.

Отдельное правило:

- `runtime_mode` отвечает только за policy исполнения;
- retrieval scope (`session_rag` / `knowledge_base_rag`) живёт отдельно как `rag_scope`
  и не кодируется в `runtime_mode`.

### Ожидаемое поведение

- `chat_only`: workflow не стартуют вообще.
- `auto`: backend может предложить route-choice при ambiguity.
- `specialized_tasks`: backend жёстче уводит в task routing, но social/greeting не ломают active scope.

### Что менять

- Контракт запроса/ответа в [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- Session/runtime state в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- UI profile/mode controls в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)

### Definition of Done

- Пользовательский mode selector влияет только на backend policy.
- `chat_only` реально блокирует compare/equipment/document-analysis/doc-qa workflow.
- `specialized_tasks` не превращает social-replies в route drift.

## Фаза 2a. Backend-authoritative orchestration persistence

**Приоритет:** отдельный future follow-up  
**Backlog:** [B3.31a](../../TASKS.md)

### Назначение

После стабилизации execution boundary перевести authoritative workflow state
из `Chainlit`-authoritative persistence в dedicated backend store/checkpointer.

### Минимальный результат

- backend-owned `run/state` model для `state_ref`, `pending_action_id`, `resume_state`;
- stable mapping between UI thread/session and backend run;
- resume/recovery без зависимости от `Chainlit` storage schema;
- `Chainlit` как thin UI-mirror, а не owner execution state.

## Фаза 3. Зафиксировать стратегию по LangChain

**Приоритет:** высокий  
**Backlog:** [B3.32](../../TASKS.md#L534)

### Решение

Не делать full rewrite core-логики на “чистый LangChain”.

### Почему

Потому что уже существует рабочий каркас:

- [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- `LangGraph` workflow
- `AdaptiveRAGPipeline`

Полный rewrite сейчас увеличит технический шум и остановит стабилизацию.

### Что сделать

- Оформить ADR в `docs/`
- Зафиксировать допустимые зоны точечной интеграции:
  - retriever adapters
  - reranker adapters
  - eval harness
  - observability adapters

### Definition of Done

- Появился документ с explicit trade-offs.
- Есть один pilot area для LangChain без смены публичного API-контракта.

## Фаза 4. Улучшить quality routing только после стабилизации orchestration

**Приоритет:** средне-высокий  
**Backlog:** [B3.21](../../TASKS.md#L555)

### Почему это не первый шаг

Новый classifier не устранит главный источник хаоса, если route всё ещё частично решается в UI.

### Что сделать

- Собрать eval harness для intent routing.
- Сравнить baseline `LaBSE` и альтернативы на реальном наборе интентов.
- Подменять classifier только за стабильным orchestration API.

### Затрагиваемые файлы

- [backend/orchestrator/rag/classifier.py](../../backend/orchestrator/rag/classifier.py)
- [backend/orchestrator/rag/pipeline.py](../../backend/orchestrator/rag/pipeline.py)
- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- тесты в [backend/tests/](../../backend/tests/)

### Definition of Done

- Есть routing eval dataset.
- Есть сравнение хотя бы 2-3 embedding моделей.
- Замена classifier не требует переписывания UI logic.

## Фаза 5. Стабилизировать старое железо через runtime budgeting

**Приоритет:** средне-высокий  
**Backlog:** [T4.13](../../TASKS.md#L671)

### Цель

Убрать жесткие контекстные лимиты и перейти к runtime-aware token budget.

### Что сделать

- Перестать опираться на фиксированные char-based лимиты.
- Добавить `effective_context_tokens` в runtime status.
- Сделать preflight profiles:
  - `default`
  - `adaptive`
  - `manual`

### Затрагиваемые зоны

- [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- UMS status/runtime config
- RAG retrieval budget / truncation policy

### Definition of Done

- На слабом железе система деградирует по объёму контекста, а не по стабильности.
- Контекстный budget рассчитывается из runtime profile, а не из магических констант.

## Фаза 6. Только после этого добавлять UX-переключатели и profile selector

**Приоритет:** после Фаз 1-5  
**Backlog:** [T4.3](../../TASKS.md#L615)

### Что сюда входит

- Переключатель “общий чат / agent mode”
- Selector inference/profile mode
- Кнопки выбора сценария в Chainlit
- welcome / starter screen для use-case entrypoints
- `ChatSettings` tabs как mutable UX control-plane:
  - `Use Case`
  - `RAG`
  - `Model`
  - `Prompt`
  - `Generation`
- dual-source config для `system_prompt`, `temperature`, `top_p`, `max_tokens`:
  code/backend defaults + UX overrides

### Важное правило

UI-кнопки должны:

- менять backend mode/profile;
- не дублировать backend routing;
- не содержать локальной business policy.
- показывать effective config, resolved backend'ом, а не просто локально выбранные значения.

## Что не делать сейчас

Следующие задачи полезны, но не должны съесть ближайшие 2 недели раньше фаз 1-5:

- [T4.4 — UMS Model Control API](../../TASKS.md#L620)
- [T4.5 — Dynamic model registration](../../TASKS.md#L628)
- [T4.6 — Port pool и scheduler](../../TASKS.md#L634)
- [T4.8 — Observability stack](../../TASKS.md#L646)
- [T4.9 — vLLM adapter](../../TASKS.md#L653)

## Рекомендуемый порядок исполнения

1. Реализовать [B3.31](../../TASKS.md#L510)
2. На его базе ввести `auto | chat_only | specialized_tasks`
3. Зафиксировать [B3.32](../../TASKS.md#L534) через ADR
4. Затем делать [B3.21](../../TASKS.md#L555)
5. Затем делать [T4.13](../../TASKS.md#L671)
6. И только потом UI-controls из [T4.3](../../TASKS.md#L615)

## Быстрая управленческая сводка

Если формулировать совсем коротко:

- у проекта уже есть фундамент;
- главный долг сейчас не “нет агентов” и не “нет LangChain”;
- главный долг в том, что orchestration decision layer до сих пор частично живёт в UI;
- поэтому ближайшая цель не расширять систему, а довести границу между UI и backend до конца.

## Проверка после каждого этапа

Запускать минимум:

```bash
cd backend && pytest tests/ -v -m "not integration"
```

Для Chainlit/UMS stability:

```bash
cd backend && pytest tests/test_chainlit_streaming.py tests/test_unified_model_server_streaming.py tests/test_unified_model_server_startup.py -q
```

## Следующий документ

После утверждения этого плана логично сделать отдельный execution-plan на `B3.31` с покомпонентной разбивкой:

- новый orchestration response contract
- перенос routing decision из `Chainlit` в `agent_api`
- runtime mode switch
- regression tests
