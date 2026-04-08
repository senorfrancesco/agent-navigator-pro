# Open WebUI Model / Tool Split Plan

**Дата:** 2026-04-08  
**Статус:** рабочий implementation plan / source-backed decision record  
**Назначение:** зафиксировать обязательное разделение между `model provider` и `tool server` в `Open WebUI`, чтобы native tool-calling не шёл через product-specific assistant wrapper `agent-navigator`.

## 1. Problem Statement

Практический smoke показал архитектурный конфликт текущего eval-contour:

- `Open WebUI` уже умеет вызывать наш backend `OpenAPI Tool Server`;
- fast/deep tools реально доходят до `/tool-server/tools/*`;
- но выбранная в чате модель `agent-navigator` остаётся backend-specific assistant wrapper, а не raw model provider;
- из-за этого даже корректный tool result затем снова проходит через product-specific system behavior `agent-navigator`, а не через нейтральный `model + tools` flow.

Итог:

- `Open WebUI` не получает чистую модель, которая должна делать native tool calling;
- tool result пересказывается через wrapper-layer;
- deep-tool UX и follow-up behavior искажаются;
- становится трудно понять, где заканчивается поведение модели и где начинается логика `Agent Navigator backend`.

## 2. Source-Backed Decisions

### 2.1 Open WebUI treats providers and tools as separate integration surfaces

По официальным docs Open WebUI:

- model providers подключаются отдельно через `Admin Settings -> Connections` как `OpenAI` / `OpenAI-compatible` / `llama.cpp` / `vLLM` и другие protocol-level providers;
- tool servers подключаются отдельно через `OpenAPI Tool Servers`;
- `Open WebUI` прямо описывает providers как отдельный inference path, а tools как отдельный extensibility path;
- native function calling зависит от **выбранной модели** и её реальной способности качественно вызывать tools.

Источники:

- Connect a Provider: <https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/>
- Starting with OpenAI: <https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai/>
- OpenAPI Tool Servers / Open WebUI Integration: <https://docs.openwebui.com/features/plugin/tools/openapi-servers/open-webui/>

### 2.2 Open WebUI is protocol-centric, not provider-specific

Open WebUI прямо пишет, что он protocol-centric platform и ориентируется на `OpenAI Chat Completions API` как на универсальный protocol surface. Это означает:

- для native chat/tool use в `Open WebUI` нужно давать ему **protocol-clean model endpoint**;
- product-specific assistant wrapper не должен masquerade as raw provider, если поверх него ещё будут жить tool servers.

Источник:

- Starting with OpenAI: <https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai/>

### 2.3 User Tool Servers and model connections run at different network/control layers

Docs Open WebUI разделяют:

- `User Tool Servers` — browser-side;
- `Global Tool Servers` — backend-side;
- admin model connections — instance-wide/shared;
- `Direct Connections` — отдельная experimental browser-direct feature.

Из этого следует:

- canonical production/eval path для моделей не нужно строить на `Direct Connections`;
- tool servers и model providers не надо смешивать в один backend wrapper.

Источники:

- OpenAPI Tool Servers / Open WebUI Integration: <https://docs.openwebui.com/features/plugin/tools/openapi-servers/open-webui/>
- Understanding Settings: <https://docs.openwebui.com/getting-started/settings/>
- Direct Connections: <https://docs.openwebui.com/features/chat-conversations/direct-connections>
- Connection Errors: <https://docs.openwebui.com/troubleshooting/connection-error/>

### 2.4 Native tool calling depends on the selected model, and local models may perform poorly

Docs Open WebUI прямо предупреждают:

- native function calling имеет смысл только если выбранная модель реально поддерживает tool calling;
- некоторые local models “claim support but often produce poor results”.

Это подтверждает наш практический вывод: даже при корректном `tool-server` contract нельзя оставлять `agent-navigator` wrapper основным chat model для Open WebUI native tool calling.

Источник:

- OpenAPI Tool Servers / Open WebUI Integration, section “Native Function Calling”: <https://docs.openwebui.com/features/plugin/tools/openapi-servers/open-webui/>

## 3. Normative Architecture Decision

### 3.1 Canonical split

Для `Open WebUI` вводим обязательное разделение:

1. **Model provider surface**
   - raw chat model endpoint;
   - speaks `OpenAI-compatible` protocol cleanly;
   - не содержит product-specific assistant behavior `agent-navigator`;
   - подключается в `Open WebUI` через `Admin Settings -> Connections`.

2. **Tool server surface**
   - backend-owned `/tool-server`;
   - содержит `ask_document`, `analyze_*`, `compare_*`, `tool-jobs/*`;
   - подключается в `Open WebUI` как `OpenAPI Tool Server`.

3. **Specialized assistant surface**
   - `agent-navigator` wrapper остаётся отдельным specialized assistant mode / compatibility layer;
   - не используется как default model для native tool-calling path в Open WebUI.

### 3.2 What must not happen

Нельзя оставлять такую схему как canonical:

`Open WebUI -> agent-navigator wrapper -> tool server -> agent-navigator wrapper`

Потому что это приводит к:

- double orchestration;
- product-specific system prompt leakage в обычный tool workflow;
- размытию границы между `model behavior` и `backend business logic`;
- трудной отладке tool use в `Open WebUI`;
- ложному ощущению, что проблема в tools, хотя она находится в model-wrapper layer.

### 3.3 Canonical target topology

Целевая схема должна быть такой:

`Open WebUI`
-> `raw OpenAI-compatible model provider`
and separately
-> `Agent Navigator OpenAPI Tool Server`
-> `backend tool execution / jobs / retrieval / domain services`

Опционально позже:

`Open WebUI`
-> `agent-navigator specialized assistant`

Но это отдельный assistant mode, а не primary chat provider for native tools.

## 4. Recommended Runtime Modes

### 4.1 Local eval mode

- backend tool server живёт в нашем backend;
- `Open WebUI` model connection идёт на raw OpenAI-compatible endpoint;
- tools подключаются как `User Tool Server`;
- `agent-navigator` wrapper может оставаться отдельной model entry для compare/debug, но не как default.

### 4.2 Production-like mode

- model provider подключается через `Admin Settings -> Connections -> OpenAI-compatible`;
- tools подключаются как `Global Tool Server` или другой approved shared path;
- `Direct Connections` не являются canonical production path, потому что docs помечают их как experimental и browser-direct.

## 5. Raw Model Provider Options

### Option A — Reuse existing raw inference server

Если у нас уже есть endpoint, который:

- говорит по `OpenAI-compatible` protocol;
- не инжектит `agent-navigator` product behavior;
- не перехватывает domain logic;
- даёт обычный `chat completions` surface,

то именно его нужно подключать в `Open WebUI` как primary model connection.

### Option B — Introduce thin raw-chat gateway

Если текущий `/v1/chat/completions` в `agent_api.py` уже слишком связан с `agent-navigator` behavior, нужно выделить тонкий raw provider surface, например:

- новый `raw_model_api.py`;
- или отдельный route namespace в `agent_api.py`;
- или reuse существующего `UMS`, если он already cleanly exposes an OpenAI-compatible surface.

Требования к этому gateway:

- никакой backend-owned orchestration policy;
- никакого forced route/tool selection;
- никакого document lifecycle awareness;
- only model inference, model discovery, streaming, and tool-calling compatibility.

## 6. Required Implementation Slices

### Slice A — Decision / Contract Freeze

Файлы:

- `docs/plans/migration/2026-04-08-openwebui-model-tool-split-plan.md`
- `docs/plans/migration/2026-04-02-unified-openwebui-migration-master-plan.md`
- `TASKS_MIGRATION.md`

Что фиксируем:

- `model/tool split required`;
- `agent-navigator` wrapper is not the primary Open WebUI model provider;
- `Direct Connections` are experimental and not canonical.

### Slice B — Inventory Current Model Surfaces

Нужно ответить на три вопроса:

1. Есть ли уже в проекте raw OpenAI-compatible endpoint без product wrapper?
2. Можно ли использовать существующий `UMS` или другой server directly?
3. Если нет, какой самый узкий thin gateway нужен?

Acceptance:

- описан exact current provider inventory;
- выбран один canonical raw model endpoint для `Open WebUI`.

### Slice C — Implement Raw Model Connection Path

Нужно сделать один из вариантов:

- reuse existing raw provider;
- или выделить отдельный raw provider route.

Acceptance:

- `Open WebUI` может выбрать raw model, который не injects `agent-navigator` behavior;
- обычный чат через raw provider не идёт через product-specific orchestration.

### Slice D — Rewire Open WebUI Eval Contour

Нужно:

- подключить raw model provider в `Open WebUI`;
- оставить `Agent Navigator Tools` как отдельный tool server;
- убрать `agent-navigator` wrapper из роли default chat model для native tool-calling smoke.

Acceptance:

- fast tool response выглядит как normal model+tool behavior, а не как response from product wrapper;
- deep tool path доходит до `accepted job` без лишнего double-wrapping;
- если auto-polling по-прежнему отсутствует, это фиксируется как `Open WebUI UX limitation`, а не как проблема model split.

## 7. Acceptance Criteria

`Model/tool split` считается закрытым, если одновременно верны все пункты:

- в `Open WebUI` настроен отдельный raw model provider;
- `Agent Navigator Tools` остаётся отдельным `OpenAPI Tool Server`;
- выбранная для tool smoke модель больше не является `agent-navigator` wrapper;
- fast tool end-to-end response не проходит через product-specific assistant layer;
- docs/backlog фиксируют `agent-navigator` only as specialized assistant / compatibility mode.

## 8. Explicit Non-Goals

В этот plan не входят:

- `M2.1/M2.2` upload/document binding implementation;
- `MCP` transport work;
- redesign `Open WebUI` buttons/actions layer;
- full production auth/secret management redesign;
- detached background worker queue for jobs.

## 9. Practical Recommendation

Ближайший правильный шаг после этого плана:

1. не чинить дальше UX deep tools в текущем `agent-navigator` model path;
2. сначала развести raw model и product wrapper;
3. потом повторить `M3.3` smoke уже на raw provider + tool server split;
4. только затем решать, нужен ли дополнительный Open WebUI-side polling UX for async tools.
