# Open WebUI MCP / OpenAPI / Actions Architecture Plan

> **Тип документа:** подробное ТЗ на реализацию инструментов, их backend-архитектуры и подключения к `Open WebUI`.
>
> **Статус:** future scope / `M4.1` target state
>
> **Scope marker:** это не MVP и не текущий acceptance baseline. Документ описывает целевую архитектуру после стабилизации `M1-M3`, когда backend tool contracts, upload/document binding и `OpenAPI Tool Server` уже приведены в рабочее состояние.
>
> **Назначение:** описать, как в `Agent Navigator Pro` должны быть реализованы наши инструменты, какой слой должен быть каноническим, как должен выглядеть `MCP`-контур, как это подключается к `Open WebUI`, и как пользователи будут запускать сценарии через chat tools, slash-команды и видимые кнопки.

## 1. Executive Summary

Для `Agent Navigator Pro` нельзя строить основную интеграцию вокруг “наивного MCP”, где модель просто получает много инструментов и сама свободно решает, как ими пользоваться. Это даёт слишком много недетерминизма, плохо масштабируется на тяжёлых данных и размывает backend-owned orchestration policy.

Целевой дизайн должен быть таким:

- **Primary integration path:** `OpenAPI Tool Server`
- **Compatibility layer:** `MCP (Streamable HTTP)` поверх того же backend tool catalog
- **Canonical business logic:** только во внешнем backend
- **Open WebUI role:** thin UI shell + tool picker + prompt UX + action buttons + rich embeds

Из этого следует главный архитектурный вывод:

1. Мы реализуем **единый backend-owned catalog** наших инструментов.
2. Поверх него поднимаем **OpenAPI facade** как основной production/eval слой.
3. При необходимости поднимаем **MCP facade**, которая не содержит новой бизнес-логики, а только адаптирует тот же catalog к `tools/list`, `tools/call`, далее позже `resources/*` и `prompts/*`.
4. В самом `Open WebUI` используем **несколько UX-слоёв одновременно**:
   - `Global Tool Servers` для постоянных shared tools;
   - `User Tool Servers` для локальной отладки;
   - `Workspace Prompts` для slash-команд и typed-forms;
   - `Action Functions` для видимых кнопок и follow-up действий;
   - `Rich UI embeds` для графиков, таблиц, скачивания отчётов и интерактивных result cards.

## 2. Source-Backed Decisions

### 2.1 Official Open WebUI decisions

- Open WebUI docs прямо рекомендуют `OpenAPI` как preferred path для большинства deployment-сценариев, а `MCP` рассматривать как отдельный совместимый transport-layer choice, а не как дефолт для production.
- Native `MCP` в Open WebUI работает через `MCP (Streamable HTTP)` и требует `WEBUI_SECRET_KEY`.
- `Global Tool Servers` в Open WebUI скрыты по умолчанию и активируются пользователем через `+` в поле ввода чата.
- `User Tool Servers` выполняются из браузера пользователя, `Global Tool Servers` выполняются с backend стороны Open WebUI.
- `Workspace Prompts` дают slash-команды с typed input variables и popup-формами.
- `Action Functions` дают видимые кнопки в UI и могут вызывать внешние API, работать с файлами, уважать permissions и возвращать rich embeds.
- `Rich UI Embedding` позволяет tools/actions возвращать `HTMLResponse` и рисовать iframe-based UI прямо в чате.

### 2.2 MCP specification decisions

- MCP tools не навязывают конкретную UI-модель, но клиент должен явно показывать пользователю доступные инструменты, их вызовы и sensitive operations.
- MCP tools должны иметь явные схемы входных параметров.
- MCP tool results должны поддерживать `text`, `image`, `resource`.
- Сервер обязан валидировать inputs, делать access control, rate limiting и sanitization outputs.

### 2.3 Lessons from the two local PDF articles

Из статей в PDF следует несколько практически важных выводов:

- Наивный агент “дадим модели пул инструментов и всё взлетит” плохо работает на реальных данных.
- Для production-like задач нужен **доменный tool decomposition**, а не только один generic DB/MCP tool.
- Для больших данных и тяжёлых ответов нельзя бездумно гонять большие payloads через обычный `tool_calling`.
- Полезно отделять:
  - planner;
  - executor;
  - deterministic domain tools;
  - UI rendering / download / chart generation.
- Визуализация, скачивание и UI eventing лучше реализуются в отдельном UI/rendering слое, а не через raw textual tool output.
- При росте числа инструментов качество выбора падает, если нет явного route-scoping, planner discipline и валидации.

## 3. Non-Negotiable Architecture Rules

### 3.1 Канонический backend

Все domain workflows, routing decisions, state, jobs, uploads, document binding и permissions должны жить в `Agent Navigator Pro backend`, а не в Open WebUI plugin code.

### 3.2 No business logic inside Open WebUI

В `Open WebUI` допустимы только:

- UI glue
- prompt UX
- button actions
- HTML embeds
- вызов backend endpoints
- per-chat enablement / tool selection

Нельзя переносить в Open WebUI:

- настоящую логику анализа документов
- stateful orchestration
- planner/executor pipeline
- job state storage
- authoritative file lifecycle

### 3.3 OpenAPI-first, MCP-second

Primary path должен быть:

- `Open WebUI -> OpenAPI Tool Server -> backend adapters -> orchestration/domain services`

Secondary path:

- `Open WebUI MCP client -> MCP facade -> same backend adapters -> same orchestration/domain services`

То есть `MCP` не отдельная бизнес-система, а transport adapter.

### 3.4 Tool catalog must be narrow and domain-specific

Вместо “одного большого универсального оркестратора” пользователю и модели нужны **узкие понятные инструменты**.

Первый обязательный каталог:

- `ask_document`
- `analyze_document_fast`
- `analyze_document_deep`
- `compare_documents_fast`
- `compare_documents_deep`
- `analyze_equipment_fast`
- `analyze_equipment_deep`

## 4. Target System Architecture

### 4.1 Logical layers

1. **Domain / orchestration layer**
   - existing backend orchestration and domain services
   - upload/document lifecycle
   - report generation
   - citations / provenance
   - async jobs

2. **Tool adapter layer**
   - canonical registry of tools
   - per-tool request validation
   - per-tool response normalization
   - sync vs async split

3. **Transport facades**
   - OpenAPI Tool Server
   - MCP Streamable HTTP facade

4. **Open WebUI UX layer**
   - Global Tool Servers
   - User Tool Servers
   - slash prompts
   - action buttons
   - rich UI embeds

### 4.2 Proposed backend modules

Рекомендуемая структура:

- `backend/orchestrator/tool_catalog.py`
  - registry of canonical tools
- `backend/orchestrator/tool_schemas.py`
  - Pydantic request/response schemas
- `backend/orchestrator/tool_execution.py`
  - dispatcher into existing backend services
- `backend/orchestrator/tool_jobs.py`
  - async job contract and polling facade
- `backend/orchestrator/openapi_tools_api.py`
  - OpenAPI endpoints
- `backend/orchestrator/mcp_api.py`
  - MCP transport adapter
- `backend/orchestrator/tool_rich_ui.py`
  - optional HTML/embed builders for charts/tables/download cards

### 4.3 Canonical internal execution flow

`Open WebUI`
-> chooses tool / prompt / button
-> transport call (`OpenAPI` or `MCP`)
-> backend tool adapter validates input
-> adapter resolves document context / upload refs / model profile
-> adapter calls orchestration/domain service
-> service returns:
   - quick result
   - or accepted job
-> backend returns normalized payload
-> Open WebUI renders:
   - plain answer
   - rich result card
   - action buttons
   - downloadable/report artifacts

## 5. Tool Taxonomy

### 5.1 Core tools

#### `ask_document`

Назначение:

- grounded Q&A по одному или нескольким документам
- краткий цитируемый ответ

UX role:

- основной “чатовый” tool
- доступен как tool toggle и как slash-паттерн `/askdoc`

Default execution mode:

- synchronous

Returns:

- answer text
- citations
- source metadata
- optional follow-up actions

#### `analyze_document_fast`

Назначение:

- быстрый поверхностный анализ документа
- summary, key findings, risks, anomalies

UX role:

- one-shot tool
- slash prompt `/analyze_doc`
- можно вызывать action-кнопкой “Быстрый анализ”

Default execution mode:

- synchronous

#### `analyze_document_deep`

Назначение:

- глубокий анализ документа
- многоступенчатый reasoning
- опциональный отчёт

UX role:

- long-running action
- вызывается как tool или через action “Глубокий анализ”

Default execution mode:

- async job

#### `compare_documents_fast`

Назначение:

- быстрый дифф/сравнение 2 документов по ключевым пунктам

UX role:

- synchronous comparison card

#### `compare_documents_deep`

Назначение:

- большой cross-document comparison
- матрицы расхождений
- структурированный отчёт

UX role:

- async job
- result artifact + summary message

#### `analyze_equipment_fast`

Назначение:

- быстрый разбор единицы оборудования / спецификации / карточки

#### `analyze_equipment_deep`

Назначение:

- глубокий анализ с многошаговым reasoning, рисками, compatibility and recommendation outputs

### 5.2 Auxiliary tools

Не в первой MVP-фазе, но архитектурно ожидаемы:

- `resolve_documents`
- `resolve_equipment_entities`
- `get_job_status`
- `download_report`
- `render_chart`
- `publish_result_artifact`

Эти инструменты не должны сразу торчать наружу, если их можно держать internal-only.

## 6. Contract Model

### 6.1 Common request envelope

Каждый внешний tool contract должен опираться на общий envelope:

- `tool_name`
- `requested_tool`
- `routing_mode`
- `conversation_context`
- `document_refs`
- `user_inputs`
- `response_mode`

Рекомендуемые поля:

- `requested_tool: str`
- `routing_mode: "explicit" | "assisted" | "auto"`
- `document_ids: list[str]`
- `upload_ids: list[str]`
- `job_mode: "sync_if_possible" | "force_async"`
- `ui_hints: dict`

### 6.2 Sync result contract

Для quick tools:

- `status: "completed"`
- `tool_name`
- `assistant_message`
- `structured_result`
- `citations`
- `artifacts`
- `available_actions`
- `execution_metadata`

### 6.3 Async result contract

Для heavy tools:

- `status: "accepted"`
- `tool_name`
- `job_id`
- `status_url`
- `poll_after_ms`
- `result_preview`
- `available_actions`

### 6.4 Job polling contract

- `job_id`
- `status: queued | running | completed | failed | cancelled`
- `stage`
- `progress_pct`
- `message`
- `result_ref`
- `error_summary`
- `artifacts`

### 6.5 Rich UI result contract

Если результат визуальный:

- backend может вернуть:
  - `embed_url`
  - или inline HTML payload
  - или artifact URL + metadata

Но LLM-facing context не должен теряться. Если в Open WebUI используется rich embed, нужен и textual/structured companion context, чтобы модель понимала, что было сгенерировано.

## 7. Open WebUI Integration Surfaces

## 7.1 Global Tool Servers

Это основной путь для shared deployment.

Использовать для:

- production/shared tools
- общих document/equipment workflows
- единых admin-managed integrations

Важно:

- global tools скрыты по умолчанию
- пользователь включает их через `+` внизу чата
- они активируются per-chat

Вывод для нас:

- основной каталог инструментов должен существовать как `Global Tool Server`
- user onboarding должен явно объяснять, что нужные инструменты активируются через `+`

## 7.2 User Tool Servers

Использовать для:

- developer-local debugging
- персонифицированной отладки
- временных localhost setups

Не считать основным production UX.

## 7.3 Slash commands via Workspace Prompts

Slash-команды нужны не вместо tools, а **поверх tools** как guided UX.

Рекомендуемые prompt commands:

- `/askdoc`
- `/analyze_doc`
- `/compare_docs`
- `/analyze_equipment`

Каждый prompt:

- показывает typed form
- собирает параметры пользователя
- формирует канонический запрос к модели
- подталкивает LLM к explicit tool invocation

Slash layer нужен потому что:

- пользователям легче работать с короткой командой, чем помнить JSON/tool args
- Open WebUI уже умеет popup-form UX из коробки
- это даёт controllable discoverability без хака input area

## 7.4 Visible buttons via Action Functions

Скриншоты показывают важный UX-вопрос: пользователю нужны не только slash-команды и tool toggles, но и **видимые кнопки**.

Нативная модель должна быть такой:

- **не пытаться превращать tool servers в кастомные input buttons**
- использовать:
  - `Action Functions` для видимых кнопок
  - `Global Tool Servers` для actual tool execution

Action buttons подходят для:

- `Быстрый анализ`
- `Глубокий анализ`
- `Сравнить`
- `Построить график`
- `Скачать CSV`
- `Скачать PDF`
- `Переиспользовать этот документ`
- `Добавить в knowledge base`

Action functions должны быть **тонким glue-слоем**:

- принимать текущий message/body/file context
- вызывать наш backend endpoint
- возвращать rich embed, файл, или follow-up message

Нельзя писать в action-кнопках основную бизнес-логику.

## 7.5 Rich UI embeds

Использовать для:

- таблиц сравнения
- графиков
- download cards
- report summary dashboards
- interactive result cards

Применение:

- tool returns structured result + embed context
- action returns HTMLResponse for explicit user-driven rendering

## 8. MCP Design for Our Product

### 8.1 What MCP should do here

`MCP` должен выполнять роль совместимого tool/resource transport для клиентов, которым нужен именно MCP.

Наш MCP server должен:

- объявлять `tools`
- позже объявлять `resources`
- возможно позже объявлять `prompts`
- вызывать тот же canonical backend tool catalog

### 8.2 What MCP should NOT do here

MCP server не должен:

- содержать отдельный planner
- писать свою логику маршрутизации
- хранить свой job state
- отдельно управлять документами
- дублировать validation rules

### 8.3 MCP feature phases

#### Phase A

- `tools/list`
- `tools/call`
- только 7 core tools

#### Phase B

- `resources/list`
- `resources/read`
- document templates / KB summaries / report artifacts

#### Phase C

- `prompts/list`
- `prompts/get`
- если окажется полезно синхронизировать slash-like patterns на уровне MCP clients

## 9. OpenAPI Design for Our Product

### 9.1 Why OpenAPI remains primary

Потому что для наших инструментов важны:

- строгие схемы
- понятные HTTP semantics
- idempotency
- auditability
- quotas/rate limiting
- auth and reverse proxy friendliness
- predictable testing

### 9.2 OpenAPI endpoint strategy

Рекомендуемый путь:

- один `OpenAPI Tool Server` app/route group
- один route per public tool

Например:

- `POST /tools/ask_document`
- `POST /tools/analyze_document_fast`
- `POST /tools/analyze_document_deep`
- `POST /tools/compare_documents_fast`
- `POST /tools/compare_documents_deep`
- `POST /tools/analyze_equipment_fast`
- `POST /tools/analyze_equipment_deep`
- `GET /tools/jobs/{job_id}`

Не использовать один giant `/orchestrate` как основной внешний interface.

### 9.3 OpenAPI + MCP coexistence

Рекомендуемая реализация:

- канонический execution adapter один
- OpenAPI router и MCP router оба используют один dispatcher
- integration tests должны проверять parity результата

## 10. UX Mapping for Each Tool

### 10.1 Ask Document

Open WebUI entry points:

- Global Tool toggle
- `/askdoc`
- optional action button `Спросить по документу`

### 10.2 Analyze Document Fast

Entry points:

- Global Tool toggle
- `/analyze_doc`
- action button `Быстрый анализ`

### 10.3 Analyze Document Deep

Entry points:

- action button `Глубокий анализ`
- optional slash `/analyze_doc_deep`
- async result card with polling

### 10.4 Compare Documents Fast

Entry points:

- `/compare_docs`
- action button `Сравнить`

### 10.5 Compare Documents Deep

Entry points:

- action button `Глубокое сравнение`
- async report generation

### 10.6 Analyze Equipment Fast / Deep

Entry points:

- `/analyze_equipment`
- model-specific action buttons

## 11. File / Upload / Document Context Rules

Пока migration не закрыла `M2.*`, нельзя делать вид, что Open WebUI сам authoritative-owner файлов.

Правила:

- Open WebUI attachment = только входной UX
- authoritative file identity = backend-owned
- backend выдаёт `upload_id`, `document_id`, `document_version_id`
- tools принимают backend refs, не raw filesystem paths
- Open WebUI actions/slash/tools должны оперировать этими refs

До завершения `M2.*` допустим temporary compatibility layer, но только как transitional adapter.

## 12. Auth / Security / Networking

### 12.1 Open WebUI requirements

- задать `WEBUI_SECRET_KEY`
- не путать MCP connection type и OpenAPI connection type
- помнить, что Open WebUI native MCP = только Streamable HTTP

### 12.2 Tool server auth

Для production/shared path:

- backend tool server должен иметь отдельный auth contract
- желательно service token / signed header / reverse proxy auth
- не оставлять чувствительные инструменты на anonymous access

### 12.3 Global vs User networking

Для `User Tool Servers`:

- запросы идут из браузера
- нужен CORS
- `localhost` валиден только если Open WebUI открыт с того же localhost-клиента

Для `Global Tool Servers`:

- запросы идут из backend Open WebUI
- `localhost` — это localhost самого Open WebUI backend/container
- если Open WebUI в Docker, нужны `host.docker.internal` или реальный host/IP

### 12.4 Sensitive operations

Sensitive actions должны требовать explicit user visibility/confirmation:

- экспорт
- удаление/публикация файлов
- запись в knowledge base
- внешние side effects

## 13. Recommended Implementation Strategy

### Phase 0 — Finalize contract

- утвердить этот ТЗ
- зафиксировать final tool schemas
- зафиксировать sync vs async split

### Phase 1 — Backend canonical catalog

- реализовать `tool_catalog.py`
- реализовать `tool_schemas.py`
- реализовать dispatcher
- реализовать `/tools/jobs/{job_id}`

### Phase 2 — OpenAPI Tool Server

- поднять отдельный OpenAPI router
- включить OpenAPI docs
- покрыть routing priority tests
- добавить auth/CORS policy

### Phase 3 — Open WebUI shared integration

- подключить backend как `Global Tool Server`
- описать exact URLs
- включить нужные tools per chat
- проверить toggle UX через `+`

### Phase 4 — Slash UX

- создать `Workspace Prompts`
- оформить typed forms
- добавить prompt catalog и naming convention

### Phase 5 — Action buttons

- добавить minimal `Action Functions`
- только UI glue + backend calls
- внедрить `Быстрый анализ`, `Глубокий анализ`, `Скачать`, `Переиспользовать`

### Phase 6 — MCP facade

- добавить Streamable HTTP MCP adapter
- `tools/list` / `tools/call`
- parity tests with OpenAPI

### Phase 7 — Rich result cards

- HTML embeds
- tables/charts/download cards
- custom result context

## 14. Testing and Verification

### 14.1 Backend contract tests

- schema validation
- routing priority
- sync result shape
- async accepted-job shape
- error shape

### 14.2 Transport parity tests

- OpenAPI result vs MCP result for same tool input
- identical business result, different transport envelope only

### 14.3 Open WebUI integration smoke

- tool server visible in input indicator
- global tool toggle through `+`
- slash prompt opens typed form
- action button appears under message
- rich embed renders correctly

### 14.4 Security tests

- CORS policy
- auth rejection
- rate limiting
- role restrictions for global tools/actions

## 15. Deliverables

Обязательные deliverables этой инициативы:

1. `OpenAPI Tool Server`
2. canonical tool catalog
3. MCP compatibility facade
4. slash prompt catalog
5. action button catalog
6. rich result card patterns
7. integration guide for Open WebUI admins

## 16. Final Recommendation

Для `Agent Navigator Pro` правильная стратегия не “строить всё через MCP”, а:

- **ядро делать через backend-owned OpenAPI tool catalog**
- **MCP реализовать как совместимый слой поверх него**
- **slash-команды делать через Open WebUI Prompts**
- **видимые кнопки делать через Action Functions**
- **сложные визуальные результаты делать через Rich UI embeds**

Это даёт:

- предсказуемый backend
- контролируемый tool selection
- понятный UX в Open WebUI
- возможность shared deployment
- совместимость и с OpenAPI, и с MCP
- минимальный риск превратить Open WebUI в второй orchestration engine

## 17. Sources

Локальные документы:

- `/home/seral/HDD/proj/agent-navigator-pro/Реализация MCP в Open WebUI. Часть 1. Интеграция c Open WebUI _ Хабр.pdf`
- `/home/seral/HDD/proj/agent-navigator-pro/Реализация MCP в Open WebUI. Часть 2 — Агентское поведение _ Хабр.pdf`

Official docs:

- Open WebUI MCP: <https://docs.openwebui.com/features/extensibility/mcp/>
- Open WebUI OpenAPI integration: <https://docs.openwebui.com/features/extensibility/plugin/tools/openapi-servers/open-webui/>
- Open WebUI Prompts: <https://docs.openwebui.com/features/workspace/prompts/>
- Open WebUI Action Functions: <https://docs.openwebui.com/features/plugin/functions/action/>
- Open WebUI Rich UI: <https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/>
- MCP tools specification: <https://modelcontextprotocol.io/specification/2024-11-05/server/tools>
- MCP resources specification: <https://modelcontextprotocol.io/specification/2025-06-18/server/resources>
- MCP transports specification: <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>
