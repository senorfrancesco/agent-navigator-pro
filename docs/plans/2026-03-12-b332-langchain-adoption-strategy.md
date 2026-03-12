# B3.32 — LangChain Adoption Strategy (ADR)

## Status

Accepted on 2026-03-12.

## Context

В репозитории уже есть рабочий orchestration core:

- `Chainlit` как основной UI
- `agent_api.py` как transport/compatibility layer
- `orchestration_runtime.py` как decision layer
- `execution_runtime.py` как unified execution layer
- `LangGraph` workflows для `compare`, `equipment`, `document_analysis`
- `AdaptiveRAGPipeline` как собственный retrieval/runtime contour

При этом в зависимостях уже присутствуют `langchain`, `langgraph` и `langchain-mcp-adapters`, а в документации регулярно возникает соблазн “допереписать систему на LangChain”, чтобы якобы получить оркестрацию, tools и agent runtime.

После закрытия `B3.31` главный архитектурный риск уже не в “отсутствии LangChain”, а в том, чтобы снова не создать второй orchestration core рядом с тем, который только что стабилизирован.

## Problem

Нужно зафиксировать, где использование `LangChain`/`LangGraph` допустимо и полезно, а где оно недопустимо, потому что ломает backend-first orchestration boundary или провоцирует full rewrite.

Без такого решения появятся три типовых анти-паттерна:

1. Новый routing/policy слой на `LangChain agents` рядом с `orchestration_runtime.py`.
2. Попытка перенести execution contract в generic `LLM + tools` loop.
3. Смешивание `Chainlit`/API transport concerns с `LangChain` abstractions вместо сохранения thin adapter model.

## Decision

Не делать full rewrite orchestration core на “чистый LangChain”.

`LangGraph` остаётся допустимым и уже используемым средством для локальных deterministic workflows.  
`LangChain` допускается только как набор точечных integration adapters вокруг существующего core, но не как новый source of truth для routing, execution state или публичного runtime contract.

## What Stays Canonical

Каноническими слоями системы остаются:

- backend-first routing/policy в `orchestration_runtime.py`
- unified execution contract в `execution_runtime.py`
- `Chainlit` как thin renderer/control-plane
- `AdaptiveRAGPipeline` как собственный retrieval contour
- domain workflows в `backend/orchestrator/workflows/*`

Любая интеграция `LangChain`/`LangGraph` должна встраиваться в эти границы, а не заменять их.

## Allowed Integrations

Разрешены только точечные интеграции, которые не меняют публичный execution contract и не создают второй orchestration brain.

### 1. LangGraph для локальных workflow-графов

Это уже используется и остаётся допустимым:

- [compare.py](../../backend/orchestrator/workflows/compare.py)
- [equipment.py](../../backend/orchestrator/workflows/equipment.py)
- [document_analysis.py](../../backend/orchestrator/workflows/document_analysis.py)

Условие: `LangGraph`-граф остаётся domain-level implementation detail, а не заменяет orchestration boundary проекта.

### 2. Retriever / reranker adapters

Допустимо использовать `LangChain`-совместимые retriever/reranker adapters там, где это даёт интеграционный выигрыш, но при сохранении текущего ownership:

- query → orchestration policy остаётся в backend core
- retrieval semantics остаются в `AdaptiveRAGPipeline`
- adapter не должен становиться новым RAG runtime “вместо” существующего pipeline

### 3. Eval harness и offline experimentation

Допустимы `LangChain`-совместимые evaluation utilities для:

- routing/retrieval eval
- answer faithfulness / groundedness experiments
- benchmark harness вокруг candidate components

Условие: eval-слой не должен диктовать runtime architecture.

### 4. Observability / callbacks / tracing adapters

Допустимы точечные callback/observability adapters, если они:

- не меняют execution semantics
- не вносят dependency inversion между UI и backend
- не становятся обязательным runtime-layer для обычного запроса

### 5. Pilot area с чёткой изоляцией

Допускается один pilot area для точечной интеграции `LangChain`, если одновременно выполняются условия:

- pilot изолирован от основного execution contract
- есть чёткий rollback path
- не меняется внешний API/user-flow
- pilot измеряется через tests/evals, а не через “ощущение удобства”

Рекомендуемый pilot area: retrieval/eval adapters, а не routing/execution core.

## Explicitly Forbidden

Следующее запрещено в рамках этой стратегии:

### 1. Full rewrite orchestration core на LangChain agents

Нельзя заменять:

- `orchestration_runtime.py`
- `execution_runtime.py`
- current backend response contract

на generic `AgentExecutor`, tool loop или иной `LangChain-first` runtime.

### 2. Перенос routing/policy decisions в LangChain layer

Intent routing, ambiguity handling, `choose_route`, `upload_required`, `runtime_mode`, `rag_scope` и related policy должны оставаться backend-owned logic.

### 3. UI-driven LangChain runtime

Нельзя строить отдельный `LangChain` execution path из `Chainlit`, минуя unified backend execution core.

### 4. Замена AdaptiveRAGPipeline “ради унификации”

Нельзя выбрасывать текущий `AdaptiveRAGPipeline` только потому, что `LangChain` умеет retrievers/chains. Сначала нужен доказанный технический выигрыш на конкретном pilot area.

### 5. Подмена backend state model LangGraph/LangChain persistence без фазы `B3.31a`

Execution-critical state нельзя “незаметно” перевести в LangGraph/LangChain persistence раньше, чем будет завершён backend-owned orchestration state store.

## Rationale

### Почему не rewrite

Потому что нужный orchestration core уже существует, и именно его split-brain только что закрывался в `B3.31`. Новый rewrite сейчас:

- повторно размоет ownership границы
- создаст второй execution contract
- затормозит `B3.31a`, `T4.13`, `T4.2`, `T4.3`
- смешает migration work с product/runtime stabilization

### Почему не ban altogether

Потому что `LangGraph` уже полезен в domain workflows, а `LangChain` может дать практическую пользу в adapters/evals/observability. Полный запрет здесь был бы таким же ошибочным, как и rewrite.

## Consequences

### Positive

- снимается давление “переписать всё на LangChain”
- сохраняется один orchestration core
- остаётся пространство для точечной интеграции, где она действительно полезна
- следующие фазы roadmap не блокируются архитектурным churn

### Negative

- часть `LangChain` возможностей сознательно остаётся неиспользованной
- интеграции придётся делать аккуратно через adapters, а не через “быстрый переподъём” всей архитектуры

## Implementation Guidance

Если появляется предложение внедрить `LangChain`/`LangGraph`, его нужно проверять по короткому фильтру:

1. Меняет ли это routing/policy/execution contract?
2. Появляется ли второй orchestration brain?
3. Становится ли UI зависимым от новой runtime semantics?
4. Есть ли изолированный pilot area и rollback path?
5. Есть ли измеримый выигрыш, а не только удобство разработки?

Если хотя бы на один из первых трёх вопросов ответ “да”, изменение не соответствует `B3.32`.

## Next Step

После этой фиксации следующим архитектурным блоком остаётся `B3.31a`:

- backend-owned orchestration state store
- resume/checkpoint semantics вне UI ownership

То есть `B3.32` не открывает новый runtime rewrite-track, а наоборот фиксирует границы перед дальнейшей backend-stabilization работой.
