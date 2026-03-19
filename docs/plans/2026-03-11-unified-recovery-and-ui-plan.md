# Unified Recovery And UI Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Собрать единый антикризисный план для Agent Navigator Pro на основе `task.md`, текущего `TASKS.md` и stabilization-плана, чтобы выровнять архитектуру оркестрации, стабилизировать runtime и добавить управляемые UI-возможности в Chainlit без повторного смешивания слоёв.

**Architecture:** В репозитории уже есть рабочий фундамент через `Chainlit + Agent API + LangGraph + AdaptiveRAGPipeline + UMS`, но часть orchestration logic до сих пор находится в UI. План исходит из тревог и вопросов в `task.md`: оркестратор ощущается “размытым”, UI берёт на себя route decisions, routing quality ещё недостаточна, а старое железо страдает от жёстких runtime-лимитов. Поэтому порядок фиксируется жёстко: сначала orchestration boundary, затем classifier quality как следующий главный приоритет, потом runtime modes и context budgeting, и только поверх этого UI controls для mode/profile/prompt.

**Tech Stack:** Python 3.11, FastAPI, Chainlit, LangGraph, AdaptiveRAGPipeline, UMS, pytest.

---

## Исходный запрос из `task.md`

В `task.md` зафиксированы четыре реальные боли:

1. Система ощущается как набор костылей и ошибок без ясной приоритизации.
2. Непонятно, где находится “настоящий” оркестратор: в backend или в UI.
3. Есть сомнение, что текущего classifier недостаточно для корректной оркестрации.
4. Есть ощущение, что адаптация под старое железо зашла в тупик.

Дополнительно из `task.md` следует product/UI-запрос:

1. Нужен переключатель между общим чатом и агентной системой.
2. Нужны интерактивные кнопки выбора сценария.
3. Нужна возможность задавать через UI хотя бы часть model/runtime параметров, как в Open WebUI.

## Что уже подтверждено кодом и backlog

### Оркестратор в проекте есть, но он раздвоен

- Backend объявлен как orchestrator в [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- UI содержит существенную часть route/policy/state-логики в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)

Ключевые точки, где UI сейчас участвует в orchestration:

- intent detection: [backend/orchestrator/chainlit_app.py:508](../../backend/orchestrator/chainlit_app.py#L508)
- route choice prompt/state: [backend/orchestrator/chainlit_app.py:596](../../backend/orchestrator/chainlit_app.py#L596)
- intent decision policy: [backend/orchestrator/chainlit_app.py:702](../../backend/orchestrator/chainlit_app.py#L702)
- runtime dispatch в `on_message`: [backend/orchestrator/chainlit_app.py:1690](../../backend/orchestrator/chainlit_app.py#L1690)
- local executor dispatch: [backend/orchestrator/chainlit_app.py:1763](../../backend/orchestrator/chainlit_app.py#L1763)

### Основные backlog-задачи уже совпадают с болью из `task.md`

1. [B3.31 — API Orchestration Layer + Runtime Mode Switch](../../TASKS.md#L510)
2. [B3.32 — LangChain adoption strategy](../../TASKS.md#L534)
3. [B3.21 — Quality upgrade: отдельная embedding-модель для intent classification](../../TASKS.md#L555)
4. [T4.2 — Chainlit UX hardening](../../TASKS.md#L609) — implemented
5. [T4.3 — LLM Profile Selector в Chainlit](../../TASKS.md#L615) — implemented
6. [T4.13 — Runtime Context Budget + Preflight Profiles](../../TASKS.md#L671)

### Вывод

`task.md` не описывает ложную проблему. Он очень точно указывает на незавершённый архитектурный переход.

## Антикризисные правила на ближайшие 2 недели

1. Не добавлять новые workflow и новых “агентов”, пока не завершён orchestration split.
2. Не делать full rewrite на LangChain.
3. Не тащить UI дальше в routing logic.
4. Не улучшать classifier до стабилизации orchestration contract.
5. Сразу после стабилизации orchestration contract приоритет смещается на classifier quality, потому что именно classifier определяет корректность `action_required`.
6. Не решать проблемы старого железа случайными prompt/runtime-limit костылями.
7. Любые UI controls должны быть thin client над backend policy.
8. Основной контур разработки и правок сейчас: нативный `Chainlit` на хосте, а не Docker-контейнер.
9. Контейнерный `Chainlit` использовать как финальный packaging/prod-validation этап после стабилизации нативного runtime.

## Целевое состояние

### Как должен работать оркестратор

Оркестратор должен принимать:

- user query
- attachments / active docs
- runtime mode
- rag scope
- optional model profile
- session state

И возвращать единый decision contract:

- `mode`
- `route`
- `executor`
- `model_profile`
- `rag_scope`
- `knowledge_collection_id`
- `source_scope_summary`
- `sources`
- `confidence`
- `trace_id`
- `state_ref`
- `pending_action_id`
- `action_required`
- `fallback`

### Как должен работать UI

UI не должен решать:

- какой workflow запускать
- нужен ли fallback
- какой classifier verdict приоритетнее
- какую модель/профиль подставить по route

UI должен:

- отправлять user input и selected controls
- рендерить backend decision
- показывать progress/steps
- показывать action buttons / settings
- сохранять thread/session UX

### Как работать с Chainlit на этом этапе

На текущем этапе разработки правки нужно вносить и проверять прежде всего в нативном запуске `Chainlit`, а не в контейнере.

Почему:

- быстрее цикл правок и отладки;
- проще локализовать проблемы orchestration/runtime без docker-layer шума;
- легче различать баги UI/runtime и баги packaging/deployment.

Контейнерный запуск нужен:

- для финальной проверки production-path;
- для проверки auth/history/compose integration;
- для итоговой валидации после завершения основных правок в orchestration и UI.

## Фаза 1. Закрыть split-brain между UI и backend

**Приоритет:** максимальный  
**Backlog:** [B3.31](../../TASKS.md#L510)

### Проблема

Сейчас `Chainlit` по факту является вторым оркестратором.

### Что делать

1. Перенести decision layer в [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py).
2. Оставить в [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py) только rendering, session UX и UI controls.
3. Свести `route`, `mode`, `rag_scope`, `knowledge_collection_id`, `source_scope_summary`,
   `action_required`, `confidence`, `model_profile`, `state_ref`, `pending_action_id`
   к одному backend response contract.
4. Убрать прямую зависимость backend/workflow-слоя от `Chainlit` runtime API там, где она ломает headless execution и unit-тесты.

### Отдельное уточнение по persistence ownership

Для `resume`, `human-in-the-loop`, `action_required`, `rag_scope` и `knowledge_collection_id`
нужен один authoritative слой состояния.

Для текущего `B3.31` был выбран сознательно ограниченный pragmatic step, но он уже закрыт
отдельной фазой [B3.31a](../../TASKS.md):

- execution-critical state перенесён в backend-owned `orchestrator_runs`;
- `Chainlit` session state теперь служит UI-mirror/cache;
- `Chainlit` resume сначала читает backend snapshot и только потом использует legacy fallback.

Целевая модель после `B3.31a`:

- backend persistence/checkpointer является source of truth;
- `Chainlit` session state — только UI-mirror/cache для удобства рендера;
- восстановление pending action, selected `rag_scope`, active collection и route state
  идёт из backend state по `state_ref`, а не из UI-only эвристик;
- подтверждение действий должно использовать стабильный `pending_action_id`,
  чтобы resume и follow-up не расходились с backend contract.

### Отдельное уточнение по workflow progress

Ошибка вида `ChainlitContextException` в unit-тестах показывает, что часть workflow-узлов
ещё использует `cl.Step(...)` как runtime dependency, а не как optional UI adapter.

Значит в рамках стабилизации orchestration boundary нужно довести правило до конца:

- workflow/backend слой должен уметь исполняться без активного `Chainlit` context;
- progress reporting должен идти через adapter/callback/no-op abstraction;
- `Chainlit` может подключать UI-реализацию progress steps, но не должен быть
  обязательной зависимостью для headless backend execution и unit-тестов.

### Затрагиваемые файлы

- [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- [backend/tests/test_document_analysis.py](../../backend/tests/test_document_analysis.py)
- [backend/tests/test_chainlit_streaming.py](../../backend/tests/test_chainlit_streaming.py)
- [backend/tests/test_unified_model_server_streaming.py](../../backend/tests/test_unified_model_server_streaming.py)
- [backend/tests/test_unified_model_server_startup.py](../../backend/tests/test_unified_model_server_startup.py)

### Definition of Done

- UI больше не содержит локальные business-ветки orchestration.
- Один и тот же запрос при одинаковом `runtime_mode` даёт одинаковый backend-route.
- Все route-choice prompt’ы строятся из backend decision, а не из локальных if-веток UI.
- Workflow-узлы не требуют живого `Chainlit` context для unit-тестов и headless execution.
- Legacy `/v1/chat/completions` не живёт отдельным runtime-миром:
  endpoint остаётся только compatibility surface и делегирует execution в unified backend core.
- Legacy upload discovery для `/v1/chat/completions` не должен быть implicit default:
  recent uploads из `open_webui_uploads` допустимы только как явный compatibility opt-in.
- `backend/tests/test_chainlit_streaming.py` завершается cleanly без hanging pytest-process.

## Фаза 2. Ввести runtime modes как backend policy

**Приоритет:** очень высокий  
**Backlog:** [B3.31](../../TASKS.md#L510)

### Цель

Ввести три режима:

- `auto`
- `chat_only`
- `specialized_tasks`

### Поведение

- `chat_only`: никакие compare/equipment/document-analysis/doc-qa workflow не стартуют.
- `auto`: backend сам принимает route decision, при ambiguity требует подтверждение.
- `specialized_tasks`: backend ориентирован на task-routing, но social/greeting не ломают active scope.

`runtime_mode` здесь отвечает только за policy исполнения.
Он не должен кодировать retrieval scope вроде `session_rag` или `knowledge_base_rag`.
Эта ось должна жить отдельно в backend contract как `rag_scope`, иначе split-brain вернётся уже через UI settings.

### Затрагиваемые файлы

- [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)

### Почему это связано с `task.md`

Это прямой ответ на запрос “переключатели с общего чата на агентную систему”.

## Фаза 2a. Вынести orchestration state в backend-owned persistence

**Приоритет:** отложенный follow-up после стабилизации boundary  
**Backlog:** [B3.31a](../../TASKS.md)

### Суть

После закрытия текущего pragmatic `B3.31` нужно перевести authoritative ownership для
`state_ref`, `pending_action_id`, `resume_state` и checkpoint/recovery semantics
из `Chainlit`-adjacent persistence в dedicated backend store.

### Почему это отдельная фаза

- текущий `B3.31` специально ограничен execution boundary и thin-UI refactor;
- попытка в том же scope ещё и мигрировать persistence размоет delivery и verification;
- backend-owned state store нужен как долгосрочная архитектурная цель, а не как скрытый
  side effect текущего refactor.

### Что должно получиться

- backend workflow/run state становится source of truth;
- `Chainlit` остаётся presentation/control surface и optional chat-history layer;
- resume/recovery работает без чтения execution-critical state из UI storage;
- замена frontend не требует изменения core orchestration logic.

## Фаза 3. Зафиксировать стратегию по LangChain

**Приоритет:** высокий  
**Backlog:** [B3.32](../../TASKS.md#L534)

### Решение

Не делать full rewrite core на “чистый LangChain”.

### Допустимые направления точечной интеграции

- retriever/reranker adapters
- eval harness
- observability adapters

### Что оформить

- ADR в `docs/`
- short rationale в `TASKS.md`

### Статус

Закрыто через [2026-03-12-b332-langchain-adoption-strategy.md](./2026-03-12-b332-langchain-adoption-strategy.md).

### Почему это важно

Это снимает ложное давление “у нас нет оркестратора, значит надо срочно переписать всё на LangChain”.

## Фаза 4. Улучшить routing quality через отдельный classifier contour

**Приоритет:** следующий шаг сразу после стабилизации orchestration boundary  
**Backlog:** [B3.21](../../TASKS.md#L555)

### Что делать

1. Подготовить eval harness для интентов.
2. Поддержать runtime policy `embedder | llm | hybrid`.
3. Сравнить текущий baseline, LLM-router и альтернативные embedding модели.
4. Внедрять classifier upgrade только после стабилизации orchestration contract.

### Зафиксированная стратегия

- `prod default`: `embedder`
- `intent embedder`: `Qwen3-Embedding-0.6B`
- `legal/doc similarity embedder`: `LaBSE`
- `optional profile / experiment`: `pure llm`
- `research track`: сравнение новых embedders и улучшение `hybrid`

Почему:

- `LLM-router` лучше понимает семантику, но для дорогих workflow опасно делать его единственным default без собственного eval;
- локальный benchmark показал, что текущий `hybrid` уступает лучшему pure embedder и по quality, и по latency;
- production quality здесь определяется не общей `accuracy`, а ценой ошибки маршрутизации.

### Что обязательно добавить в policy

1. `abstain / unsure / needs_confirmation` state вместо принудительного top-1 выбора при ambiguity.
2. Cost-sensitive routing metrics:
   - `FP(compare_documents)`
   - `FP(equipment_analysis)`
   - `FP(document_question)`
3. Cost-weighted routing score, а не только top-1 accuracy.
4. Для `hybrid` отдельные показатели:
   - `% routed by llm`
   - `% embedder fallback`
   - `% unsure`

### Рекомендуемый eval order

#### Wave 1

- текущий centroid baseline
- `llm`
- `hybrid`

Цель: подтвердить, что новая policy-line лучше текущего baseline на реальных routing-cases.

#### Wave 2

- `intfloat/multilingual-e5-large-instruct`
- `Qwen3-Embedding-0.6B`
- 1 более старший `Qwen3-Embedding-*`
- `BAAI/bge-m3`

Цель: понять, есть ли смысл менять embedder contour.

#### Wave 3

- SetFit few-shot classifier
- calibrated hybrid with abstain thresholds

Цель: production optimization после baseline comparison.

### Затрагиваемые файлы

- [backend/orchestrator/rag/classifier.py](../../backend/orchestrator/rag/classifier.py)
- [backend/orchestrator/rag/pipeline.py](../../backend/orchestrator/rag/pipeline.py)
- [backend/tests/](../../backend/tests/)

### Важное ограничение

Classifier upgrade не должен снова утянуть decision-making в UI.

## Фаза 5. Стабилизировать старое железо через runtime budgeting

**Приоритет:** после Фаз 1-4  
**Backlog:** [T4.13](../../TASKS.md#L671)

### Что делать

1. Перейти от жёстких лимитов к `effective_context_tokens`.
2. Добавить preflight profiles:
   - `default`
   - `adaptive`
   - `manual`
3. Ограничивать retrieved context по runtime budget, а не по магическим константам.

Статус на 2026-03-13:

- `T4.13` реализован как backend-owned runtime budget contract;
- `UMS /status` публикует `runtime_profile`, `effective_context_tokens`,
  `retrieved_context_tokens_budget`, `generation_tokens_reserve`, `context_budget_ratio`;
- `Chainlit` и `AdaptiveRAGPipeline` используют эти поля для token-derived context budget;
- profile selection на этой фазе ещё env-driven/backend-first, без user-facing selector.

### Затрагиваемые зоны

- [backend/orchestrator/agent_api.py](../../backend/orchestrator/agent_api.py)
- UMS status/runtime config
- RAG truncation policy

### Почему это отвечает на `task.md`

Это прямой ответ на вопрос “почему на старом железе всё ощущается невозможным”.

### Дополнение по scripts / launcher strategy

Под адаптацию под оборудование нужен не набор разрозненных shell/python-скриптов, а единый runtime entrypoint.

Целевое требование:

- один preflight/runtime-config script для hardware detection + profile planning + `.env.runtime`
- один launcher entrypoint, который умеет:
  - `native` dev-path
  - `container` prod-validation path
  - optional preflight
  - health-check/report

Статус на 2026-03-13:

- `T4.14` реализован;
- canonical runtime path теперь `scripts/launcher.sh` + `scripts/runtime_preflight.py`;
- `backend/.env.runtime` используется как applied runtime output;
- `run_all.sh`, `run_native.sh`, `run_container.sh` оставлены как compatibility wrappers;
- richer user-facing runtime/profile selection остаётся на `T4.3`.

При этом текущий этап разработки остаётся native-first:

- для dev/debug основной путь: нативный `Chainlit`
- для packaging/prod: контейнерный path в конце

### Рекомендуемая структура

- `scripts/runtime_preflight.py` или эволюция `scripts/preflight.py`
- `scripts/run_native.sh`
- `scripts/run_container.sh`
- thin wrapper `scripts/run_all.sh` только как back-compat alias

Или альтернативно:

- один `scripts/launcher.sh`/`scripts/bootstrap.sh` с флагом `--target native|container`

Но даже в этом варианте нельзя снова делать Docker-first для основного dev-loop.

## Фаза 6. Улучшить RAG, но без перескока через orchestration

**Приоритет:** после Фаз 1-5  
**Основание:** `task.md` + текущие RAG-задачи

### Что делать

1. Диагностировать реальный context payload, который уходит в LLM.
2. При необходимости добавить reranker path.
3. Ужесточить grounded-answer policy для document QA.
4. Явно разделить два продуктовых режима:
   - `session RAG` по активным документам текущего чата;
   - `knowledge-base RAG` по заранее подготовленной базе источников.
5. Зафиксировать retrieval eval для `LaBSE` vs `Qwen3-Embedding-0.6B`,
   прежде чем менять dense retrieval embedder.
6. Привести названия `tiers` в соответствие реальному runtime, не переобещая
   `agentic/multi-agent` там, где сейчас только iterative retrieval.

### Затрагиваемые файлы

- [backend/orchestrator/rag/retriever.py](../../backend/orchestrator/rag/retriever.py)
- [backend/orchestrator/rag/pipeline.py](../../backend/orchestrator/rag/pipeline.py)
- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)

### Ограничение

Это не должно подменять собой закрытие `B3.31`.

### Уточнение по RAG-архитектуре

Для этого проекта нужно держать две разные продуктовые сущности, но не смешивать их с `runtime_mode`:

1. `Session RAG`
   - документы загружены прямо в текущем чате;
   - ingestion происходит сразу после upload;
   - retrieval scope ограничен `active_doc_ids`;
   - это основной ближайший UX-path для `Chainlit`.
2. `Knowledge-base RAG`
   - документы проходят ingestion в постоянную базу;
   - есть `sources + chunks + embeddings + content_hash + index_version`;
   - schema должна хранить минимум:
     `embedding_model_id`, `retrieval_embedder_profile`, `chunking_version`;
   - поиск идёт по заранее подготовленной коллекции, но при наличии активных документов
     в сессии выполняется merged retrieval: `knowledge base + session overlay`;
   - каждый источник в ответе обязан нести provenance:
     `source_origin = session | knowledge_base`;
   - это отдельный продуктовый режим, а не просто “ещё одна настройка retrieval”.

Архитектурное правило:

- `route=document_question` остаётся общим;
- `runtime_mode` задаёт политику запуска;
- `rag_scope` задаёт область retrieval;
- `knowledge_base_rag` не создаёт отдельный route, а переключает retrieval contour и evidence policy.

Merged retrieval policy должна быть зафиксирована явно, а не оставлена “на усмотрение реализации”.
Минимальный required contract:

- отдельный candidate budget для `session` и `knowledge_base` контуров;
- дедупликация идентичных/почти идентичных фрагментов до финального prompt;
- нормализация score между контурами до merge/fusion;
- общий rerank/quality gate после merge shortlist;
- citation tie-break rule:
  при одинаковом фрагменте предпочитать session-copy как primary source,
  а KB-источник показывать как supporting duplicate.

Следствие для UI:

- эти режимы должны быть видимы как разные chat/workspace modes, а не скрытая эвристика;
- `session RAG` и `knowledge-base RAG` лучше проектировать как отдельные вкладки/профили,
  рядом с другими типами чатов, чтобы пользователь понимал scope поиска;
- backend должен возвращать `source_scope_summary`, чтобы UI мог явно показать,
  что ответ собран из `session`, `knowledge_base` или обоих контуров;
- citations/evidence блок нужен в обоих режимах.

Status on 2026-03-13:

- `B3.33` выполнен как V1:
  - backend-owned KB source registry и ingestion helper добавлены;
  - `knowledge_base_rag` интегрирован в unified backend execution core;
  - merged retrieval `knowledge_base + session overlay` работает без отдельного UI/API contour;
  - provenance `source_origin | collection_id | display_name` проходит в doc-QA sources.
- `B3.35/B3.36` выполнены:
  - public-facing tiers переведены на honest wording (`iterative retrieval`, `planned multi-agent`) без смены machine-readable keys;
  - evidence UX v2 показывает `retrieval_scope`, provenance и `section/page` там, где metadata доступны.
- Follow-up оставлен отдельно:
  - persisted embeddings / vector index / reranker;
  - более глубокий KB retrieval hardening и richer citation cards поверх текущего evidence UX.

### Что считать правильным UX для document QA

Минимальный правильный grounded flow:

1. Upload -> parse -> chunk -> embed -> index.
2. Question -> retrieve top-k.
3. Filter by threshold / quality gate.
4. Prompt only with подтверждённым evidence.
5. Return answer + citations + source cards.

Что показывать в UI:

- документ;
- chunk / section / page, если доступно;
- excerpt;
- relevance / confidence;
- не показывать “процент использованного текста” как псевдоточную метрику.

### Минимальный eval matrix для retrieval и grounded QA

До внедрения `knowledge_base_rag` и до смены retrieval embedder нужно иметь
минимум следующие eval-срезы:

- `session_only`
- `knowledge_base_only`
- `mixed`
- `unanswerable`
- `duplicate-heavy`

Для mixed/evidence сценариев дополнительно нужны:

- accuracy по `source_origin`
- стабильность `source_scope_summary`
- answer faithfulness / groundedness
- citation usefulness на контрольном наборе вопросов

Status update 2026-03-13:
- minimal curated retrieval eval harness and dataset implemented;
- first `LaBSE` vs `Qwen3-Embedding-0.6B` CPU run on the curated matrix showed parity;
- retrieval dense embedder is therefore not unified yet; `LaBSE` remains the baseline until the dataset is expanded;
- eval harness now uses strict dataset validation and deterministic JSON reporting, so future migration decisions must go through this surface rather than ad hoc spot-checks.

### Что уточнить по tiers

Текущая честная интерпретация:

- `Tier 1` = basic retrieval
- `Tier 2` = corrective retrieval
- `Tier 3` = iterative retrieval
- `Tier 4` = planned multi-agent

До отдельной реализации нельзя описывать `Tier 3/4` как полноценный production-grade
agentic / multi-agent runtime.

## Фаза 7. Добавить управляемые UI-возможности в Chainlit

**Приоритет:** после Фаз 1-5, частично можно проектировать заранее  
**Backlog:** [T4.2](../../TASKS.md#L609) — implemented, [T4.3](../../TASKS.md#L615) — implemented

### Что именно нужно добавить

#### 1. Runtime Mode Selector

Переключатель:

- `Общий чат`
- `Авто`
- `Агентный режим`

Маппинг:

- `Общий чат` -> `chat_only`
- `Авто` -> `auto`
- `Агентный режим` -> `specialized_tasks`

#### 2. Model Profile Selector

Вместо raw model-id дать выбор профилей:

- `default-chat`
- `long-context`
- `legal-compare`
- `low-vram`

UI должен отправлять только profile id, а backend уже маппит его на:

- `model_id`
- `temperature`
- `context budget`
- `device_mode`

#### 3. Prompt Profile Selector

Не давать raw system prompt по умолчанию. Вместо этого:

- `default-assistant`
- `strict-grounded-doc-qa`
- `legal-analyst`
- `equipment-compliance`

Raw `system_prompt` допустим только как admin/debug control.

#### 4. Chat Settings Panel

Использовать нативные UI controls Chainlit:

- tabs как control-plane слой между `Chainlit` и backend contract:
  - `Use Case`
  - `RAG`
  - `Model`
  - `Prompt`
  - `Generation`
- `Select` для runtime mode
- `Select` для assistant/use-case mode:
  - `general_chat`
  - `coding`
  - `agentic`
  - `specific_tasks`
  - `rag_qa`
- `Select` или tabs для rag scope
- `Select` для model profile
- `Select` для prompt profile
- `Slider` / `NumberInput` для `temperature`, `top_p`, `max_tokens`
- `Switch` для `streaming` / `strict-grounding`
- `TextInput` / `TextArea` для `custom_system_prompt`

Важно:

- эта панель не является local-router;
- это UI control-plane / settings overlay поверх backend policy;
- все настройки должны попадать в backend как структурированный state, а не как ad-hoc UI flags.

Рекомендуемый state shape:

- `assistant_mode`
- `runtime_mode`
- `rag_scope`
- `model_profile`
- `prompt_profile`
- `generation_overrides`
- `custom_system_prompt`
- `tool_scope`

Поддержать dual-source конфигурацию:

- через код / backend defaults и profile config;
- через UX overrides в `ChatSettings`.

Правило precedence:

1. backend hard defaults
2. profile defaults
3. UX overrides
4. executor/workflow enforced overrides
5. backend safety validation / clamping

UI должен показывать пользователю effective values, а не делать вид,
что последнее локальное значение гарантированно применилось.

#### 5. Action Buttons для выбора сценария

Использовать `AskActionMessage` для:

- `Сравнить документы`
- `Проверить ТЗ vs КП`
- `Задать вопрос по документам`
- `Сделать сводку`
- `Отмена`

#### 5a. Welcome Screen / Starter Cards

Нужен явный приветственный экран с вариантами использования модели.
Это не замена `ChatSettings`, а быстрый входной слой.

Starter cards:

- `Общий чат`
- `Coding Assistant`
- `Agentic`
- `Специфические задачи`
- `RAG Q&A`

Каждая карточка должна предзаполнять:

- `assistant_mode`
- `runtime_mode`
- `rag_scope` при необходимости
- `model_profile`
- `prompt_profile`

#### 6. Chat Profiles

Использовать `ChatProfile` только для coarse entrypoint:

- `Agent Navigator`
- `Document Analyst`
- `Ops / Debug`

`Session RAG` и `Knowledge Base` лучше выражать как `rag_scope` selector / tabs / workspace modes
внутри профиля, а не как основной mutable `ChatProfile`.
Иначе `ChatProfile` превратится во второй локальный router и будет слишком жёстко
привязывать retrieval scope к старту треда.

Аналогично:

- `system_prompt`, `temperature`, `top_p`, `max_tokens` не должны жить в `ChatProfile`;
- они должны быть частью `ChatSettings` / backend settings state.

### Принципиальное ограничение

Эти UI controls не должны принимать route decisions локально.  
Они должны только передавать в backend:

- runtime mode
- rag scope
- profile selection
- optional overrides

### Затрагиваемый файл

- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)

## Review открытых PR по scripts/runtime

Ниже фиксируется текущее решение по открытым PR, чтобы после реализации не потерять, что именно закрывать и почему.

### PR #7 — основной кандидат на перенос идей

Ссылка: GitHub PR `#7`  
Название: `Add preflight runtime profile, effective-context budgeting, and RAG runtime integration`

Что полезно:

- связывает `preflight` с `effective_context_tokens`
- расширяет UMS `/status`
- вяжет Chainlit RAG budgeting с runtime budget
- добавляет документацию и тесты

Что требует аккуратной доработки:

- интеграцию нужно довести до native-first launcher strategy
- preflight и launcher нельзя оставлять как отдельные конкурирующие контуры
- итоговое имя/роль скрипта надо согласовать с единым dev/prod workflow

Решение:

- использовать как основной source branch по runtime budgeting ideas
- после переноса в целевой unified implementation закрыть PR как merged или superseded-by-final depending on delivery path

### PR #5 — частично полезен, но не должен жить отдельно

Ссылка: GitHub PR `#5`  
Название: `feat(scripts): add preflight and .env.runtime support in run_all`

Что полезно:

- фазы `detect | plan | apply | report`
- запись в `backend/.env.runtime`
- документация по профилям и режимам

Главный риск:

- отдельный `preflight.py` без полной интеграции с UMS/runtime contract создаёт второй источник истины
- использует private API `TierSelector._build_config()` по описанию PR

Решение:

- не мержить как отдельный продуктовый контур
- забрать из него только идеи detect/plan/report UX
- после переноса нужного закрыть как superseded

### PR #4 — слишком узкий и фактически перекрывается PR #7

Ссылка: GitHub PR `#4`  
Название: `feat(model-manager): effective context budgeting in UMS and Chainlit RAG`

Что полезно:

- сам direction по `effective_context_tokens` верный

Почему не тянуть как отдельный PR:

- PR `#7` покрывает тот же контур шире
- отдельный merge PR `#4` увеличит риск конфликтов и дробления runtime contract

Решение:

- считать superseded by `#7`/final unified implementation
- после переноса финального решения закрыть

### PR #3 — архитектурно конфликтует с текущим native-first курсом

Ссылка: GitHub PR `#3`  
Название: `refactor(scripts): unify launcher entrypoint by ui profile`

Что в нём полезно:

- идея единого launcher entrypoint сама по себе хорошая

Что конфликтует с текущим планом:

- PR строится вокруг выбора UI-профиля `chainlit|openwebui`
- по текущему решению основной dev-loop уже должен быть `native Chainlit`, а не Docker UI profile switching
- это конфликтует с текущими локальными скриптами [scripts/run_native.sh](../../scripts/run_native.sh) и [scripts/run_container.sh](../../scripts/run_container.sh)

Решение:

- не мержить в текущем виде
- переиспользовать только идею общего launcher, но переориентировать его на `native|container`, а не `chainlit|openwebui`
- после внедрения финального launcher закрыть как superseded

### PR #6 — нельзя мержить без переработки

Ссылка: GitHub PR `#6`  
Название: `refactor(scripts): simplify start_system_test via launcher and fail-fast health checks`

Главная проблема:

- PR переводит `scripts/start_system_test.sh` на вызов `run_all.sh --mode default --check-only`
- в текущем [scripts/run_all.sh](../../scripts/run_all.sh) таких флагов нет
- даже PR `#3` их не вводит

Следствие:

- PR в предложенном виде заведомо ломает test entrypoint

Решение:

- не мержить в текущем виде
- либо закрыть как invalid/superseded
- либо переоткрыть идею только после появления реального unified launcher API

## GitHub PR cleanup policy

После внедрения финального unified runtime/launcher решения нужно сделать отдельный PR-cleanup проход:

1. отметить, какие PR поглощены финальной реализацией
2. закрыть дублирующие PR как `superseded`
3. в каждом закрытом PR оставить короткий комментарий:
   - что именно было реализовано
   - в каком финальном PR/commit это теперь живёт
   - почему этот PR закрывается без merge

Приоритетный список для последующего cleanup:

- PR `#3`
- PR `#4`
- PR `#5`
- PR `#6`
- PR `#7` финально решить: merge-base или superseded-final

## Полный текущий PR inventory

На текущий момент в репозитории:

- открыто `20` draft PR
- закрыто только `2` PR (`#1`, `#2`), оба без merge

### Кластер A. Runtime / scripts / hardware adaptation

- PR `#3` — unified launcher by UI profile
- PR `#4` — effective context budgeting in UMS and Chainlit
- PR `#5` — preflight + `.env.runtime` support
- PR `#6` — start_system_test wrapper + fail-fast checks
- PR `#7` — preflight runtime profile + effective-context budgeting + RAG integration

Решение:

- рассматривать как один общий delivery cluster
- canonical final target: unified runtime/preflight/launcher implementation
- не мержить по отдельности без согласования

### Кластер B. Routing config / classifier config externalization

- PR `#8` — externalize chainlit routing keywords to yaml
- PR `#9` — строгая валидация `intent_examples.yaml`
- PR `#10` — load document type keywords from YAML config

Решение:

- не приоритет до закрытия `B3.31`
- возможны как donor branches для cleanup конфигов после стабилизации orchestration contract

### Кластер C. Docs/tasks bookkeeping

- PR `#11` — partial completion notes / TD updates
- PR `#21` — UX tasks and session log entry

Решение:

- не рассматривать как самостоятельные продуктовые PR
- переносить только итоговые документальные решения после реальной реализации

### Кластер D. Resume / session recovery / history restore

- PR `#12` — session recovery contract and state serialization
- PR `#16` — tests for chat resume context
- PR `#17` — guard restored context before doc intents
- PR `#18` — unify DB path and history troubleshooting
- PR `#19` — unify chat resume UX in Chainlit
- PR `#20` — resume document-context restore flow
- PR `#22` — handle missing resume files and RAG degradation

Решение:

- это единый resume/history cluster
- нельзя принимать по кускам без общей target-архитектуры session state
- после стабилизации orchestration boundary нужен отдельный consolidated pass по resume UX

### Кластер E. Chat summary memory

- PR `#13` — summary-memory + tests
- PR `#14` — resummarization thresholds and metrics
- PR `#15` — incremental summary memory

Решение:

- это отдельная feature line
- сейчас не приоритет, пока не стабилизированы orchestration и resume/session contract

## Приоритет PR-кластеров

1. Кластер A — runtime / scripts / hardware adaptation
2. Фазы 1-2 orchestration boundary из этого плана
3. Кластер D — resume / session recovery
4. Кластер B — routing/classifier config externalization
5. Кластер E — summary memory
6. Кластер C — docs-only cleanup

## Матрица по всем 20 открытым PR

| PR | Кластер | Кратко | Предварительный статус | Что забираем | Когда разбирать |
| --- | --- | --- | --- | --- | --- |
| `#3` | A | unified launcher by UI profile | `supersede later` | идею единого launcher entrypoint | вместе с `T4.14` |
| `#4` | A | effective context budgeting in UMS + Chainlit | `donor / supersede later` | runtime budgeting core | вместе с `T4.13` |
| `#5` | A | preflight + `.env.runtime` support | `donor / supersede later` | detect/plan/apply/report flow | вместе с `T4.13/T4.14` |
| `#6` | A | start_system_test fail-fast wrapper | `do not merge as-is` | idea of fail-fast health-check | после unified launcher API |
| `#7` | A | preflight runtime profile + budgeting + RAG integration | `main donor candidate` | strongest base for runtime contour | сейчас, в первую очередь |
| `#8` | B | routing keywords -> YAML | `defer` | externalized routing config | после `B3.31` |
| `#9` | B | strict validation for `intent_examples.yaml` | `likely keep / donor` | fail-fast config loading | после `B3.31`, до classifier upgrade |
| `#10` | B | document type keywords from config | `defer` | parser/document-type config externalization | после orchestration stabilization |
| `#11` | C | TASKS docs about TD-15..17 | `docs-only donor` | final backlog wording if still relevant | после реализации |
| `#12` | D | resume v1 contract + serialized session state | `main donor candidate` | metadata-first resume contract | после стабилизации orchestration boundary |
| `#13` | E | summary-memory + tests | `defer` | tests/invariants and memory direction | после resume/session contract |
| `#14` | E | summary-memory thresholds and metrics | `defer` | rollout flags and metrics ideas | после определения summary strategy |
| `#15` | E | incremental summary memory in Chainlit | `defer` | session summary state ideas | после оценки cluster E целиком |
| `#16` | D | tests for chat resume context | `donor` | resume regression tests | вместе с cluster D consolidation |
| `#17` | D | guard restored context before doc intents | `likely keep / donor` | context guardrails for degraded resume | вместе с cluster D consolidation |
| `#18` | D | DB path unification + history troubleshooting | `partial donor` | stable DB/history guidance | после native/container policy finalization |
| `#19` | D | unified chat resume UX in Chainlit | `defer / donor` | unified resume status messaging | вместе с consolidated resume UX |
| `#20` | D | resume document-context restore flow + smoke test | `donor` | manual restore action + smoke coverage | вместе с cluster D consolidation |
| `#21` | C | TASKS UX/history backlog section | `docs-only donor` | final task wording if still needed | после cluster D decisions |
| `#22` | D | missing resume files + RAG degradation handling | `likely keep / donor` | missing-file handling and RAG degradation guards | вместе с cluster D consolidation |

### Как читать статусы

- `main donor candidate`: наиболее ценный PR внутри кластера, вероятный основной источник кода/идей
- `donor`: полезен как источник отдельных частей, но не как самостоятельный merge-path
- `likely keep / donor`: может частично войти почти целиком, но только после проверки against final architecture
- `defer`: полезно, но не приоритетно до закрытия более базового контура
- `supersede later`: не мержить как есть; закрыть после финальной consolidation
- `do not merge as-is`: в текущем виде содержит архитектурный или контрактный разрыв

## Матрица переноса: PR -> файлы -> что переносим

### Кластер A. Runtime / scripts / hardware adaptation

| PR | Файлы | Переносим | Не переносим | Canonical target |
| --- | --- | --- | --- | --- |
| `#3` | `scripts/bootstrap.sh`, `scripts/lib/common.sh`, `scripts/run_all.sh`, `scripts/run_openwebui.sh` | идею общего launcher entrypoint, общие health/wait helpers, вынос повторяющейся shell-логики | `UI profile = chainlit|openwebui` как главный концепт запуска, Docker-first path для dev | `scripts/launcher.sh` или consolidation в [scripts/run_native.sh](../../scripts/run_native.sh) + [scripts/run_container.sh](../../scripts/run_container.sh) |
| `#4` | `backend/services/model_manager/unified_model_server.py`, `backend/orchestrator/chainlit_app.py`, `TASKS.md` | `effective_context_tokens`, публикацию budget в UMS `/status`, чтение budget из Chainlit/RAG | узкий isolated merge без preflight/unified launcher context | [backend/services/model_manager/unified_model_server.py](../../backend/services/model_manager/unified_model_server.py), [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py) |
| `#5` | `scripts/preflight.py`, `scripts/run_all.sh`, `README.md`, `TASKS.md` | фазную модель `detect|plan|apply|report`, `.env.runtime`, operator-facing UX preflight | private API `TierSelector._build_config()` и отдельный preflight contour без UMS runtime contract | `scripts/runtime_preflight.py` или unified `scripts/preflight.py`, плюс launcher integration |
| `#6` | `scripts/start_system_test.sh`, `TASKS.md` | fail-fast health-check semantics, idea of thin test wrapper | несуществующие флаги `run_all.sh --mode --check-only`, broken contract | [scripts/start_system_test.sh](../../scripts/start_system_test.sh) после появления real launcher API |
| `#7` | `scripts/preflight.py`, `scripts/run_all.sh`, `docs/preflight_runtime_profile.md`, `backend/services/model_manager/unified_model_server.py`, `backend/orchestrator/chainlit_app.py`, тесты | strongest integrated runtime contour: preflight, budget, UMS status, Chainlit RAG integration, tests | как отдельный final merge без native-first launcher refactor | основной donor для `T4.13/T4.14` в canonical runtime path |

### Кластер B. Routing / classifier config

| PR | Файлы | Переносим | Не переносим | Canonical target |
| --- | --- | --- | --- | --- |
| `#8` | `backend/orchestrator/chainlit_app.py`, `backend/orchestrator/data/routing_keywords.yaml`, `backend/tests/test_document_analysis.py`, `TASKS.md` | вынесение routing-keywords в YAML, loader, тесты на config-driven routing | сохранение UI-local routing как финальной архитектуры | после `B3.31`: либо backend orchestrator config, либо общий routing-config модуль |
| `#9` | `backend/orchestrator/rag/classifier.py`, `backend/tests/test_intent_classifier.py`, `TASKS.md` | strict validation/fail-fast для `intent_examples.yaml`, explicit config error | ничего критичного не видно; нужен compatibility check после orchestration refactor | [backend/orchestrator/rag/classifier.py](../../backend/orchestrator/rag/classifier.py) |
| `#10` | `backend/orchestrator/workflows/document_analysis.py`, `backend/orchestrator/data/parsers_config.yaml`, `backend/tests/test_document_analysis.py`, `README.md`, `TASKS.md` | externalized document type keywords, fallback config loading, targeted tests | docs/task notes как самостоятельную ценность без реальной code adoption | [backend/orchestrator/workflows/document_analysis.py](../../backend/orchestrator/workflows/document_analysis.py), [backend/orchestrator/data/parsers_config.yaml](../../backend/orchestrator/data/parsers_config.yaml) |

### Кластер C. Docs / backlog bookkeeping

| PR | Файлы | Переносим | Не переносим | Canonical target |
| --- | --- | --- | --- | --- |
| `#11` | `TASKS.md` | только финальные формулировки backlog, если остаются актуальными после consolidation | промежуточные статусы, если они уже переписаны unified-plan | [TASKS.md](../../TASKS.md) |
| `#21` | `TASKS.md` | только итоговые UX task definitions после решения по resume/history | docs-first приоритет без финальной архитектуры | [TASKS.md](../../TASKS.md) |

### Кластер D. Resume / session recovery / history restore

| PR | Файлы | Переносим | Не переносим | Canonical target |
| --- | --- | --- | --- | --- |
| `#12` | `backend/orchestrator/chainlit_app.py`, `backend/tests/test_chainlit_resume_state.py`, `TASKS.md` | metadata-first session state contract, serialized resume state, dedicated tests | всё, что привязывает route-mode/state слишком жёстко к текущему UI-local orchestration | основной donor для consolidated resume contract |
| `#16` | `backend/tests/test_chainlit_streaming.py`, `backend/tests/test_chat_resume_context.py` | regression tests for resume/history/RAG restore | assumptions, которые разойдутся с final resume contract | consolidated resume test suite |
| `#17` | `backend/orchestrator/chainlit_app.py`, `backend/tests/test_document_analysis.py`, `TASKS.md` | context guardrails при degraded resume, blocking doc-routes until context valid | UI-local decision branches, если они останутся после backend-first refactor | guardrail layer рядом с final session/context state |
| `#18` | `backend/orchestrator/chainlit_app.py`, `backend/.env.example`, `docker-compose.yaml`, `README.md`, `TASKS.md` | stable DB/history path ideas, troubleshooting notes | container-default assumptions как universal default для native dev | split between native/container docs and env defaults |
| `#19` | `backend/orchestrator/chainlit_app.py`, `backend/orchestrator/.chainlit/translations/ru-RU.json`, `backend/orchestrator/chainlit_ru-RU.md`, `TASKS.md` | unified resume status messaging, RU UX wording | отдельный UX layer без финального resume contract | final resume UX messaging in [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py) |
| `#20` | `backend/orchestrator/chainlit_app.py`, `backend/tests/test_chainlit_resume_restore_smoke.py`, `TASKS.md` | manual restore action/button, smoke-test shape | local fallback logic if it duplicates final context-state model | final restore action flow in Chainlit UI |
| `#22` | `backend/orchestrator/chainlit_app.py`, `backend/tests/test_document_analysis.py`, `TASKS.md` | missing-file handling, RAG degradation states, warnings | ad hoc state names if they conflict with final session-state contract | final degraded-resume handling layer |

### Кластер E. Summary memory

| PR | Файлы | Переносим | Не переносим | Canonical target |
| --- | --- | --- | --- | --- |
| `#13` | `backend/orchestrator/chainlit_app.py`, `backend/tests/test_chat_summary_memory.py`, `TASKS.md` | invariants/tests for summary-memory, lightweight direction | immediate merge before session architecture stabilizes | future summary-memory module in Chainlit path |
| `#14` | `backend/orchestrator/agent_api.py`, `backend/tests/test_multiturn_prompt.py`, `backend/.env.example`, `README.md`, `TASKS.md` | rollout flags, metrics, prompt-summary insertion ideas | separate summary-memory implementation in `agent_api` before deciding canonical chat path | whichever layer becomes canonical for long-chat memory |
| `#15` | `backend/orchestrator/chainlit_app.py`, `TASKS.md` | conversation summary state model, incremental update idea | LLM-based summary path before resume/session contract settles | future summary-memory state layer |

## Рекомендуемые canonical файлы после consolidation

### Runtime / hardware / launcher

- [scripts/run_native.sh](../../scripts/run_native.sh)
- [scripts/run_container.sh](../../scripts/run_container.sh)
- `scripts/preflight.py` или `scripts/runtime_preflight.py`
- [scripts/start_system_test.sh](../../scripts/start_system_test.sh)
- [backend/services/model_manager/unified_model_server.py](../../backend/services/model_manager/unified_model_server.py)
- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)

### Resume / session / history

- [backend/orchestrator/chainlit_app.py](../../backend/orchestrator/chainlit_app.py)
- новый consolidated test set в `backend/tests/`:
  - `test_chainlit_resume_state.py`
  - `test_chat_resume_context.py`
  - `test_chainlit_resume_restore_smoke.py`
  - часть кейсов в `test_chainlit_streaming.py`

### Routing / config

- [backend/orchestrator/rag/classifier.py](../../backend/orchestrator/rag/classifier.py)
- `backend/orchestrator/data/routing_keywords.yaml`
- [backend/orchestrator/data/parsers_config.yaml](../../backend/orchestrator/data/parsers_config.yaml)
- [backend/orchestrator/workflows/document_analysis.py](../../backend/orchestrator/workflows/document_analysis.py)

## Порядок фактического разбора donor PR

1. `#7`
2. `#3`
3. `#5`
4. `#4`
5. `#6`
6. `#12`
7. `#22`
8. `#17`
9. `#20`
10. `#16`
11. `#18`
12. `#19`
13. `#9`
14. `#8`
15. `#10`
16. `#13`
17. `#15`
18. `#14`
19. `#11`
20. `#21`

## Предлагаемая последовательность реализации

1. [B3.31](../../TASKS.md#L510)
2. [B3.21](../../TASKS.md#L555)
3. Runtime mode switch на backend contract
4. [B3.32](../../TASKS.md#L534)
5. [B3.31a](../../TASKS.md)
6. [T4.13](../../TASKS.md#L671)
7. [T4.2](../../TASKS.md#L609) — implemented
8. [T4.3](../../TASKS.md#L615) — implemented
9. UI actions / settings / profiles поверх нового contract в нативном `Chainlit`
10. Финальная container/prod validation после стабилизации нативного контура

## Что не делать сейчас

Пока не завершены Фазы 1-5, не делать приоритетом:

- [T4.4 — UMS Model Control API](../../TASKS.md#L620)
- [T4.5 — Dynamic model registration](../../TASKS.md#L628)
- [T4.6 — Port pool и scheduler](../../TASKS.md#L634)
- [T4.8 — Observability stack](../../TASKS.md#L646)
- [T4.9 — vLLM adapter](../../TASKS.md#L653)

## Внешняя валидация плана

Ниже фиксируются выводы после дополнительной сверки с актуальной документацией Chainlit,
LangGraph и внешними best practices по production agent systems и grounded RAG.

### Что в плане подтверждено

1. `Chainlit` действительно поддерживает тот тип UI controls, который предполагает план:
   - `ChatSettings`
   - `Select`
   - `Slider`
   - `Switch`
   - `TextInput`
   - `Action` / `AskActionMessage`
   - `Chat Profiles`
2. Значит сам вектор Phase 7 корректный: `runtime mode`, `model profile`, `prompt profile`
   и action-кнопки можно реализовать нативно в `Chainlit`, не уходя в кастомный frontend.
3. Практика “routing в backend, UI только отправляет controls и рендерит decision”
   подтверждается production best practices: дорогое и недетерминированное принятие решений
   нельзя размазывать по UI-слою.
4. Для long-running / resumable flows линия на `session state + persistence/checkpointing`
   также подтверждена. Это усиливает правильность отдельного resume-кластера PR.

### Что в плане нужно уточнить

1. `ChatProfile` не должен становиться местом для всей runtime-политики.
   Его лучше использовать как coarse entrypoint:
   - `Agent Navigator`
   - `Document Analyst`
   - `Ops / Debug`
   А runtime-переключатели (`chat_only | auto | specialized_tasks`, `model_profile`,
   `prompt_profile`) держать в `ChatSettings` и backend session state.
2. `AskActionMessage` подходит для human-in-the-loop подтверждений и быстрых сценариев,
   но не должен подменять backend orchestration.
   Правильная схема:
   - backend возвращает `action_required`
   - UI показывает `AskActionMessage`
   - backend ждёт явного подтверждения
3. Для resume/history логика не должна жить только в `Chainlit`.
   `LangGraph` и production best practices поддерживают вынесенный persistence/checkpoint model,
   поэтому восстановление нужно проектировать как backend contract, а не как UI-only convenience.

### Итог по корректности плана

План концептуально правильный.
Он совпадает с внешними best practices по трём ключевым направлениям:

- backend-first orchestration
- thin UI with explicit controls
- staged stabilization of RAG and runtime before feature expansion

Главное, что нужно не забыть: не перегрузить `ChatProfile` и не сделать из `ChatSettings`
второй локальный router.

## Текущее состояние RAG и целевой вариант

### Что уже есть в коде

Сейчас в репозитории уже не “нулевой RAG”, а гибридный foundation:

- [backend/orchestrator/rag/retriever.py](../../backend/orchestrator/rag/retriever.py)
  содержит hybrid retrieval:
  - BM25
  - dense embeddings
  - RRF fusion
  - Z-score grading
  - Rocchio query expansion
- [backend/orchestrator/rag/classifier.py](../../backend/orchestrator/rag/classifier.py)
  содержит centroid-based intent classifier по embeddings
- [backend/orchestrator/rag/pipeline.py](../../backend/orchestrator/rag/pipeline.py)
  уже задаёт tiered-модель:
  - `simple`
  - `corrective`
  - `agentic`
  - `multi-agent`

То есть фундамент у вас уже ближе к `corrective hybrid RAG`, чем к наивному search+prompt.

Важно: текущий продуктовый контур всё ещё ближе к `session RAG`, чем к постоянной knowledge base.
Это значит:

- документы индексируются в scope текущей сессии / активного набора;
- retrieval сейчас логически привязан к `active_doc_ids`, а не к постоянной общей базе;
- для следующего этапа нужен явный split между:
  - `Session RAG`
  - `Knowledge Base RAG`

### Что считать честной интерпретацией tiers

Пока не появится отдельный runtime для настоящего planner/tool-use/multi-agent слоя,
tiers лучше трактовать так:

- `Tier 1` = basic retrieval
- `Tier 2` = corrective retrieval
- `Tier 3` = iterative retrieval
- `Tier 4` = planned multi-agent

Это важнее маркетинговой формулировки, потому что дальнейшие задачи должны
опираться на реальный runtime, а не на желаемое название режима.

### К чему приводить RAG

Целевой production-вариант для этого проекта:

- не `naive/simple`
- не полноценный `multi-agent RAG`
- базовая цель: `corrective hybrid RAG`
- опционально: `agentic escape hatch` только для отдельных сложных сценариев

Практически это означает:

1. Основной path:
   - lightweight triage / intent layer
   - hybrid retrieval
   - relevance filtering / reranking
   - grounded answer generation
2. При ambiguity:
   - не запускать retrieval вслепую
   - задавать clarification question
3. При слабом железе:
   - не включать multi-agent orchestration
   - ограничивать retrieved context по `effective_context_tokens`
4. Agentic loop:
   - не делать дефолтом для document QA
   - держать как ограниченный fallback для сложных поисковых случаев

### Что добавить в roadmap после стабилизации classifier

1. Retrieval eval для `LaBSE` vs `Qwen3-Embedding-0.6B` на document QA / legal retrieval.
2. Явная продуктовая модель `Session RAG` vs `Knowledge Base`.
3. Source registry:
   - `sources`
   - `chunks`
   - `embeddings`
   - `content_hash`
   - `index_version`
4. Citations/evidence UX v2:
   - `document_name`
   - `page/section`
   - excerpt
   - relevance/confidence

### Почему именно так

Внешний research и NotebookLM сводятся к одной мысли:

- `multi-agent` даёт слишком много coordination overhead, latency и failure modes
  для текущего этапа и слабого железа;
- `simple RAG` недостаточен для ваших document QA кейсов;
- самый рациональный компромисс сейчас: `corrective hybrid RAG` с ambiguity triage,
  reranking/quality gating и строгим grounded-answer policy.

## Сценарии использования UI в Chainlit

Ниже фиксируются сценарии, которые соответствуют и `task.md`, и реальным возможностям `Chainlit`.

### Сценарий 1. Обычный чат без workflow

- пользователь выбирает `Общий чат`
- backend получает `runtime_mode=chat_only`
- все document/equipment/compare workflow запрещены
- Chainlit показывает только стандартный чат и базовые settings

### Сценарий 2. Авто-маршрутизация с подтверждением

- пользователь выбирает `Авто`
- backend сам оценивает route и confidence
- если уверенность низкая, backend возвращает `action_required=choose_route`
- UI показывает `AskActionMessage` с допустимыми сценариями

### Сценарий 3. Явный агентный task-mode

- пользователь выбирает `Агентный режим`
- backend предпочитает task-routing
- social/greeting не должны сбрасывать document context
- UI может показывать быстрые action-кнопки по workflow

### Сценарий 4. Документный QA с жёстким grounded mode

- выбран `prompt_profile=strict-grounded-doc-qa`
- backend включает более жёсткую policy ответа
- если контекст плохой или пустой, отвечает отказом/clarification, а не галлюцинацией

### Сценарий 5. Low-VRAM режим

- выбран `model_profile=low-vram`
- backend уменьшает budget/context/retrieval depth
- UI не меняет route-логику, только передаёт профиль

## NotebookLM: статус и ограничение

Аутентификация NotebookLM на машине валидна: `nlm login --check` подтвердил рабочий профиль
и наличие notebook'ов.

Но MCP-интеграция в этой сессии ведёт себя нестабильно:

- `server_info` и `refresh_auth` отрабатывают
- `notebook_list` через MCP возвращает пустой список
- `research_start` через MCP падал с `no confirmation from API`

Поэтому operational rule такой:

1. для NotebookLM auth считать проблему закрытой;
2. для research/query в текущей среде использовать CLI fallback через `nlm ...`,
   пока MCP server не синхронизирован корректно;
3. зафиксировать это как tooling-issue, а не как проблему самой аутентификации.

## Минимальный набор проверок

```bash
cd backend && pytest tests/ -v -m "not integration"
```

```bash
cd backend && pytest tests/test_chainlit_streaming.py tests/test_unified_model_server_streaming.py tests/test_unified_model_server_startup.py -q
```

## Краткая управленческая формулировка

Если резюмировать в одном абзаце:  
проблема проекта сейчас не в отсутствии “настоящих агентов”, а в том, что orchestration decision layer ещё не отделён от UI до конца. Поэтому ближайший путь не в расширении фич, а в завершении backend-first orchestration contract. После этого уже можно безопасно добавлять classifier quality, low-VRAM stability и полноценные Chainlit controls для mode/profile/prompt.
