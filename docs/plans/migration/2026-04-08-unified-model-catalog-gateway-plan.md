# Unified Model Catalog / Gateway Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Свести выбор моделей и OpenAI-compatible provider path к одному registry-backed каталогу в `UMS`, чтобы `Open WebUI` и внутренние клиенты читали один и тот же актуальный список моделей для `plain/raw model` contour, а `agent_api` перестал держать собственный raw model catalog.

**Architecture:** Не вводить новый сервис. Использовать существующий `UMS` как canonical `model catalog + runtime gateway`, а `backend/config/models.yaml` оставить операторским source of truth для модели, её видимости, capabilities и lifecycle policy. `agent_api` остаётся product/orchestration API, а его текущий raw-provider path становится временным compatibility shim до завершения migration cleanup slice. Этот plan не должен повторно решать задачу tool-routing или agentic decision policy; она уже закрывается отдельным responsibility split.

> **2026-04-09 update:** этот plan теперь зависит не только от `M3.5`, но и от [Open WebUI Responsibility Split Plan](/home/fisher/llm-tools-platform/docs/plans/migration/2026-04-09-openwebui-responsibility-split-plan.md). До unified catalog сначала нужно жёстко развести `plain model`, `explicit tools` и `agent mode`, иначе catalog cleanup снова смешается с hidden backend routing.

**Tech Stack:** FastAPI, `UMS`, `models.yaml`, `Open WebUI`, `llama-server`, optional `vLLM`, sentence-transformers / embedding service.

---

## 1. Context

### 1.0 Execution dependency

Этот plan не является ближайшим execution target внутри текущего migration contour.

Нормативная зависимость по порядку такая:

1. сначала `M3.5 — Open WebUI Named Tools + Bootstrap`
2. затем `M3.6 — Responsibility Split`
3. затем `M3.7 — Open WebUI-native Config`
4. затем `Unified Model Catalog / Gateway`

Причина:

- `M3.5` разблокирует реальный пользовательский flow в `Open WebUI` на уже рабочем backend path;
- `M3.6` убирает hidden backend tool routing из обычного model path;
- `M3.7` стабилизирует bootstrap/config contour и убирает ручной drift между backend secrets и imported Open WebUI state;
- текущая проблема model catalog является техдолгом и cleanup slice, а не immediate blocker для equipment tools flow;
- `Model Catalog / Gateway` затрагивает `agent_api` и `UMS`, то есть ту же зону, где живёт provider/tool integration surface, поэтому его нельзя безопасно смешивать с `M3.5`.
- native `Open WebUI` config/bootstrap должен сначала стабилизировать, какой provider вообще считается canonical, иначе catalog cleanup и admin wiring снова разъедутся.

### 1.1 Code-grounded current state

- `Open WebUI` raw provider path сейчас идёт не в `UMS`, а в `agent_api`:
  - `GET /raw/v1/models`
  - `POST /raw/v1/chat/completions`
- список raw-моделей сейчас строится в `agent_api` через `_list_raw_chat_capable_models()`, то есть отдельно от runtime inventory.
- `UMS` уже управляет lifecycle моделей, readiness, embeddings и heavy-model activation.
- `UMS` уже даёт `/v1/embeddings`, но на текущей ветке ещё не публикует canonical:
  - `GET /v1/models`
  - `POST /v1/chat/completions`
- `models.yaml` уже является registry для:
  - model ids
  - role bindings
  - preload policy
  - runtime hints
- `ui_control_plane.py` использует semantic model profiles и role bindings для product UX; это отдельная задача и не должно смешиваться с raw inference catalog.

### 1.2 Problem

Сейчас есть два разных списка моделей:

1. operator/runtime truth в `UMS`
2. public raw catalog в `agent_api`

Из-за этого:

- `Open WebUI` может видеть model ids, которые не являются реально bootable в текущем runtime state;
- public model list и runtime availability расходятся;
- raw provider path зависит от product API слоя, хотя должен зависеть от model gateway;
- дальнейшее подключение embedding/classifier сервисов остаётся архитектурно размазанным.

### 1.3 Non-goals

Этот slice не должен:

- блокировать текущие M1-M3 migration tasks;
- стартовать до закрытия `M3.5`;
- ломать существующий `Chainlit` path;
- вводить новый отдельный gateway-сервис;
- превращать `agent_api` в universal inference runtime;
- тащить полноценный ML experiment registry уровня MLflow.

## 2. Source-Backed Decisions

### 2.1 Open WebUI expects a protocol-clean OpenAI-compatible provider

Официальные docs Open WebUI исходят из стандартного provider contract и допускают curated model selection/allowlist на стороне подключения. Для нашей topology это означает: canonical provider для `Open WebUI` должен быть один `/v1` surface, а не product-specific wrapper.

Источники:

- <https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/>
- <https://docs.openwebui.com/getting-started/quick-start/starting-with-vllm/>

### 2.2 Centralized gateway + curated model list is the normal serving pattern

Для self-hosted inference стандартный путь:

- один gateway / provider surface;
- curated model list;
- runtime-specific adapters под ним.

Это подтверждают `vLLM`, `llama.cpp`, `LiteLLM` и production model-serving systems.

Источники:

- <https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html>
- <https://github.com/ggml-org/llama.cpp>
- <https://docs.litellm.ai/>
- <https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/model_repository.html>

## 3. Normative Decision

### 3.1 Canonical responsibilities

`UMS` становится canonical местом для:

- public model catalog;
- runtime-backed `/v1/models`;
- runtime-backed `/v1/chat/completions`;
- `/v1/embeddings`;
- health/readiness-backed visibility модели;
- routing в `llama-server` / `vLLM` / embedding runtime.

Для `Open WebUI` это означает:

- admin connection/config должен смотреть на один canonical raw provider;
- tool server config остаётся отдельной плоскостью и не смешивается с model inventory;
- model catalog не должен возвращать hidden agent/tool semantics.

`agent_api` остаётся местом для:

- orchestration;
- explicit agent mode / product behavior;
- tool-contract logic;
- compatibility shim на период migration.

При этом после `M3.6` он не должен оставаться местом hidden tool decision для обычного `Open WebUI` model path.

После `M3.7` он также не должен оставаться source-of-truth для user-facing model connection settings, если эти settings уже материализуются нативно в `Open WebUI`.

### 3.2 Model classes

В registry должны быть явно различимы:

- `llm`
- `vision`
- `embedding`
- `classifier`
- `reranker`

При этом public chat catalog не обязан показывать все model classes. `Open WebUI` должен видеть только модели, пригодные для user-facing chat path.

### 3.3 Updated catalog semantics

В `models.yaml` для каждой модели вводится operator-visible metadata:

- `enabled`
- `display_name`
- `public_in_chat`
- `service_class`
- `capabilities`
- `load_policy`

Минимальный пример:

```yaml
models:
  qwen14b_llm:
    model_id: qwen-14b-llm
    kind: llm
    enabled: true
    public_in_chat: true
    display_name: "Qwen 14B"
    service_class: llm
    capabilities: [chat]
    load_policy: on-demand
```

### 3.4 Dynamic provider list

`GET /v1/models` должен отдавать не "всё, что есть в YAML" и не "всё, что найдено на диске", а только модели, которые одновременно:

- `enabled: true`
- принадлежат public-serving классу
- проходят runtime compatibility check
- имеют valid backend route

Contract должен различать не только "зарегистрирована ли модель", но и её runtime state. Для этого в metadata response вводится поле:

- `status: "available" | "on-demand" | "unavailable"`

Семантика:

- `available` — модель уже загружена или warm-ready без ожидаемого cold start;
- `on-demand` — модель operator-approved и routable, но будет подниматься по первому запросу;
- `unavailable` — модель зарегистрирована, но сейчас не должна попадать в public provider list.

Минимальный metadata shape:

- `display_name`
- `status`
- `backend`
- `capabilities`

Нормативное правило для public catalog: модели со статусом `on-demand` не скрываются из `/v1/models`, но статус должен быть явно опубликован, чтобы cold start был объяснимым на уровне contract.

## 4. Target Topology

```text
models.yaml
    |
    v
UMS
  |- GET /v1/models
  |- POST /v1/chat/completions
  |- POST /v1/embeddings
  |- GET /ready/infer
  |- GET /status
    |
    +--> llama-server / local GGUF runtime
    +--> optional vLLM upstream
    +--> embedding/classifier service adapters

Clients:
  - Open WebUI
  - agent_api
  - Chainlit-internal integrations
```

## 5. Slice Boundaries

### 5.1 Phase A — Catalog semantics only

Никакого изменения текущего `Open WebUI` migration contour. Только:

- расширение schema в `models.yaml`;
- registry helpers;
- tests.

### 5.2 Phase B — UMS canonical `/v1/models`

Добавляется canonical provider inventory endpoint в `UMS`. `agent_api /raw/v1/models` временно может проксировать туда.

### 5.3 Phase C — UMS canonical `/v1/chat/completions`

`UMS` начинает публиковать direct OpenAI-compatible chat endpoint. После этого `agent_api /raw/v1/chat/completions` переводится в compatibility shim и затем удаляется в cleanup slice.

### 5.4 Phase D — Non-chat services normalization

Embedding / classifier / reranker surfaces приводятся к явному service-class contract, без требования показывать их в public chat catalog.

## 6. Tasks

### Task 1: Freeze catalog schema in the registry

**Files:**
- Modify: `backend/config/models.yaml`
- Modify: `backend/services/model_manager/model_registry.py`
- Test: `backend/tests/test_model_registry.py`

**Step 1: Add new metadata fields to selected models**

Добавить в `models.yaml`:

- `enabled`
- `display_name`
- `public_in_chat`
- `service_class`
- `capabilities`
- `load_policy`

Сначала только для already-supported моделей:

- `qwen-14b-llm`
- `qwen-vl-8b`
- `qwen3-embedding-0.6b`
- `labse-embedding`

**Step 2: Extend registry validation**

В `model_registry.py`:

- валидировать допустимые значения `service_class`
- валидировать тип `public_in_chat`
- дать helper-функции для list/filter:
  - `list_public_chat_model_ids()`
  - `get_public_chat_model_specs()`

**Step 3: Write focused tests**

Покрыть:

- successful load with new fields
- invalid `service_class`
- `public_in_chat: true` only for allowed classes

**Step 4: Verify**

Run: `pytest backend/tests/test_model_registry.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/config/models.yaml backend/services/model_manager/model_registry.py backend/tests/test_model_registry.py
git commit -m "feat(models): add public catalog metadata to registry"
```

### Task 2: Add canonical public catalog endpoint to UMS

**Files:**
- Modify: `backend/services/model_manager/unified_model_server.py`
- Test: `backend/tests/test_unified_model_server_startup.py`

**Step 1: Add `GET /v1/models`**

Endpoint должен строить response из registry-backed metadata, а не из filesystem scan.

Минимальный contract:

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen-14b-llm",
      "object": "model",
      "owned_by": "llm-tools-platform-ums",
      "display_name": "Qwen 14B",
      "status": "available",
      "capabilities": ["chat"]
    }
  ]
}
```

`status` field обязателен в provider contract и должен принимать только:

- `available`
- `on-demand`
- `unavailable`

**Step 2: Filter and publish runtime status**

Сначала разрешить conservative policy:

- модель включена
- модель public
- backend route существует

При этом:

- `available` публикуется для уже загруженных / warm-ready моделей;
- `on-demand` публикуется для healthy routable моделей, которые ещё не загружены в память;
- `unavailable` не должен попадать в public `/v1/models`.

**Step 3: Add tests**

Покрыть:

- public chat model visible
- public on-demand model visible with `status=on-demand`
- embedding model hidden from `/v1/models`
- disabled public model hidden

**Step 4: Verify**

Run: `pytest backend/tests/test_unified_model_server_startup.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py
git commit -m "feat(ums): add canonical v1 models catalog"
```

### Task 3: Add canonical chat completions endpoint to UMS

**Files:**
- Modify: `backend/services/model_manager/unified_model_server.py`
- Test: `backend/tests/test_unified_model_server_streaming.py`
- Test: `backend/tests/test_unified_model_server_startup.py`

**Step 1: Add `POST /v1/chat/completions`**

Endpoint должен:

- принимать OpenAI-compatible payload
- резолвить `model_id`
- идти в existing UMS infer path
- поддерживать `stream=true`

**Step 2: Reuse existing infer flow**

Не строить новый runtime path. Переиспользовать:

- `_start_server`
- existing concurrency controls
- existing infer payload routing

**Step 3: Add tests**

Покрыть:

- non-stream chat completion
- streaming chat completion
- unknown model -> `404`

**Step 4: Verify**

Run: `pytest backend/tests/test_unified_model_server_streaming.py backend/tests/test_unified_model_server_startup.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_streaming.py backend/tests/test_unified_model_server_startup.py
git commit -m "feat(ums): expose canonical v1 chat completions"
```

### Task 4: Convert `agent_api` raw provider routes into compatibility shim

**Precondition:** Task 3 accepted and merged.

**Files:**
- Modify: `backend/orchestrator/agent_api.py`
- Test: `backend/tests/test_agent_api_openai_compat.py`

**Step 1: Stop building a separate raw catalog in `agent_api`**

`GET /raw/v1/models` должен либо:

- проксировать `UMS /v1/models`, либо
- использовать shared helper from registry/UMS catalog layer

Но больше не иметь собственную model-inventory truth.

**Step 2: Stop owning raw chat serving logic**

`POST /raw/v1/chat/completions` должен проксировать в `UMS /v1/chat/completions`, сохраняя:

- streaming
- OpenAI-compatible contract
- backward compatibility для текущего `Open WebUI` contour

**Step 3: Add tests**

Покрыть:

- catalog proxy path
- chat proxy path
- error passthrough

**Step 4: Verify**

Run: `pytest backend/tests/test_agent_api_openai_compat.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/orchestrator/agent_api.py backend/tests/test_agent_api_openai_compat.py
git commit -m "refactor(agent-api): proxy raw provider routes to ums"
```

### Task 5: Normalize service-class boundaries for embeddings and classifiers

**Files:**
- Modify: `backend/config/models.yaml`
- Modify: `backend/services/model_manager/unified_model_server.py`
- Modify: `backend/services/model_manager/model_registry.py`
- Docs: `docs/runtime_profiles.md`

**Step 1: Formalize non-chat model classes**

Registry должен явно различать:

- `embedding`
- `classifier`
- `reranker`

Даже если физически они пока живут внутри `UMS`.

**Step 2: Add service-aware discovery helpers**

Нужны helper-функции:

- public chat catalog
- internal embedding services
- classifier services

**Step 3: Document operator policy**

Зафиксировать operator policy не prose-абзацем, а явной таблицей в `docs/runtime_profiles.md`:

| `enabled` | `public_in_chat` | `load_policy` | Поведение |
|---|---|---|---|
| false | any | any | Не показывается в public catalog и не маршрутизируется как пользовательская модель |
| true | false | warm | Internal-only service, может быть preloaded/activated для backend clients |
| true | false | on-demand | Internal-only service, стартует только по внутреннему запросу |
| true | true | warm | Видна в `/v1/models`, warm-ready или уже загружена |
| true | true | on-demand | Видна в `/v1/models`, но допускает cold start по первому запросу |

И отдельно описать:

- какие service classes допускаются в public catalog;
- что `enabled=false` имеет приоритет над остальными полями;
- что `public_in_chat=true` не разрешён для internal-only service classes без явного policy exception.

**Step 4: Verify**

Run: `python -m py_compile backend/services/model_manager/model_registry.py backend/services/model_manager/unified_model_server.py`
Expected: no output

Run: `git diff --check`
Expected: no output

**Step 5: Commit**

```bash
git add backend/config/models.yaml backend/services/model_manager/model_registry.py backend/services/model_manager/unified_model_server.py docs/runtime_profiles.md
git commit -m "refactor(models): formalize service class boundaries"
```

### Task 6: Switch Open WebUI docs/config to UMS canonical `/v1`

**Files:**
- Modify: `docs/guides/openwebui-eval-contour.md`
- Modify: `docker-compose.yaml`
- Modify: `TASKS_MIGRATION.md`

**Step 1: Update provider contract**

После готовности Task 3-4 зафиксировать canonical provider:

- `UMS /v1/models`
- `UMS /v1/chat/completions`

Временный `agent_api /raw/v1/*` оставить как compatibility-only path.

**Step 2: Update compose/env examples**

Для `Open WebUI` provider path указывать canonical `UMS` явным env contract:

```yaml
OPENAI_API_BASE_URL: http://ums:${UMS_PORT}/v1
```

**Step 3: Migration acceptance**

Acceptance:

- `Open WebUI` видит curated list из одного места
- raw catalog не расходится с runtime truth
- `agent_api` не владеет отдельным model provider inventory

**Step 4: Verify**

Run: `git diff --check`
Expected: no output

**Step 5: Commit**

```bash
git add docs/guides/openwebui-eval-contour.md docker-compose.yaml TASKS_MIGRATION.md
git commit -m "docs(migration): point openwebui provider path to ums"
```

## 7. Acceptance Criteria

- В проекте есть ровно один canonical public model catalog.
- `Open WebUI` получает model list не из `agent_api`-invented списка, а из `UMS`.
- Public provider list публикует явный `status` для каждой user-facing модели.
- `agent_api` не содержит отдельной truth-логики выбора public raw-моделей.
- `models.yaml` управляет:
  - видимостью модели
  - display name
  - service class
  - public/internal boundary
- Embedding/classifier модели остаются отдельно управляемыми сервисами и не засоряют chat catalog.
- `Chainlit` semantic profiles продолжают работать поверх role bindings и не зависят от public chat catalog.

## 8. Risks

- Если попытаться сделать всё одним diff, легко сломать текущий `Open WebUI` contour.
- Если смешать public catalog и runtime filesystem discovery, снова появится расхождение между advertised и bootable models.
- Если слишком рано убрать compatibility shim из `agent_api`, можно сломать текущие migration smoke paths.

## 9. Recommended Rollout Order

Precondition for this whole plan:

1. `M3.5 — Open WebUI Named Tools + Bootstrap` accepted and stable

Then execute:

2. Task 1
3. Task 2
4. Task 3
5. Task 4
6. Task 5
7. Task 6

Отдельно: выполнять этот план только после стабилизации текущего M1-M3 contour, после закрытия `M3.5`, и отдельно от tool-server migration slice.
