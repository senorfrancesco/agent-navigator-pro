# Open WebUI Responsibility Split Plan

**Дата:** 2026-04-09
**Статус:** canonical migration plan / 2-day execution target
**Назначение:** жёстко развести `plain model`, `native knowledge`, `explicit tools` и `agent mode`, чтобы `Open WebUI` стал целевым primary UI contour без скрытого backend-owned tool routing в обычном чате.

## 1. Goal

Сделать архитектуру понятной и устойчивой на слабых машинах:

- обычный model path не принимает backend agentic decisions;
- explicit tool path работает только как `model + tools` либо `user + tools`;
- agentic behavior остаётся отдельным opt-in режимом, а не скрытой логикой общего чата.

Итоговый target direction:

- `Open WebUI` — target primary UI;
- `Chainlit` — временный compatibility/debug shell до sunset slice;
- backend больше не смешивает product-wrapper orchestration с default `Open WebUI` chat path.
- native `Knowledge` path и explicit backend tools разведены как разные product contours.

## 2. Canonical Runtime Modes

### 2.1 Plain model

Контур:

- `Open WebUI`
- `raw OpenAI-compatible provider`
- без implicit backend tool routing

Правило:

- модель отвечает текстом;
- backend не выбирает инструмент за пользователя;
- classifier / route decision / forced tool selection здесь запрещены.

### 2.2 Native knowledge chat

Контур:

- `Open WebUI`
- Knowledge / files / collections
- external parser-ingestion service
- `Qdrant`

Правило:

- обычный question-answer по документам и KB идёт через native `Open WebUI Knowledge` contour;
- backend hidden tool routing здесь не обязателен;
- source-of-truth для корпуса и retrieval не должен жить только в chat-state UI.

### 2.3 Explicit tools

Контур:

- `Open WebUI`
- `raw model`
- imported named tools / action functions / prompts
- `OpenAPI Tool Server`

Правило:

- tool already selected by UI/model inside Open WebUI tool-calling contract;
- модель может выбирать инструмент только из набора, который уже явно передан ей `Open WebUI`;
- backend только исполняет уже выбранный tool;
- `/tool-server/tools/*` не должен включать semantic guesswork;
- этот контур нужен для structured analysis/compare/equipment workflows, а не как единственный способ поговорить с документом.

### 2.4 Agent mode

Контур:

- отдельный `agent-navigator` assistant profile;
- `/orchestrate` / `/execute_orchestration`;
- classifier + route decision + orchestration policy

Правило:

- agentic decision допустим только здесь;
- этот режим остаётся explicit opt-in, а не default execution path для `Open WebUI`.

## 3. Responsibility Boundaries

### 3.1 `UMS` / raw provider

Отвечает за:

- model inventory;
- raw inference;
- streaming / embeddings / runtime readiness.

Не отвечает за:

- implicit tool choice;
- document/task routing policy;
- product assistant behavior.

### 3.2 `agent_api`

Должен быть разделён на два явных слоя:

1. compatibility/product agent surface
2. thin adapters for explicit tool execution

Отдельный routing/control-plane для agentic решений допустим, но только как explicit `agent mode` surface, а не как hidden fallback обычного model path.

Не должен оставаться местом, где одновременно живут:

- raw provider truth;
- implicit tool routing для обычного chat path;
- explicit tool execution contract.

### 3.3 `Open WebUI`

Должен стать primary operator/user shell для:

- выбора raw/live model;
- native Knowledge / files / collections;
- подключения named tools;
- follow-up actions;
- admin-managed prompts/tools/functions configuration.

Не должен считаться единственным production backend для:

- parsing / OCR / table extraction;
- corpus versioning / dedup / audit;
- long-running ingestion lifecycle.

Для этого нужен внешний ingestion/admin contour.

## 4. Config Model

### 4.1 Что остаётся в backend / infra

- backend secrets;
- tool server auth source-of-truth;
- secrets и infra-config для `Qdrant`, parser-service (`Docling` / `Tika`) и embeddings path;
- export/control-plane metadata;
- runtime URLs и backend-owned capabilities;
- policy/feature flags, которые влияют на backend execution contract;
- `UMS` / provider runtime parameters: `num_ctx`, `num_gpu`, `num_thread`, `use_mmap`, `use_mlock`, `keep_alive` и аналогичные low-level serving knobs;
- generation / parsing / RAG source-of-truth defaults и backend clamp policy;
- `LangGraph` prompts, tool/workflow internal prompts и backend agent/system prompts;
- всё, что нельзя безопасно хранить в imported `Open WebUI` code/state.

### 4.2 Что должно жить нативно в `Open WebUI`

- tool server connection;
- workspace tools;
- action functions;
- prompts;
- Knowledge collections / file bindings / user-facing retrieval controls, если это управляется самим `Open WebUI`;
- tool/action runtime valves, где это поддерживает сам `Open WebUI`;
- enable/disable state, display metadata и non-secret runtime defaults для user-facing tool UX.

Это включает только `integration/UI-owned config`, но не делает `Open WebUI` владельцем model runtime behavior.

Нормативное правило:

- `backend/.env` может оставаться bootstrap input, но не должен быть long-term user-facing configuration surface для imported tools.

Уточнение:

- это не plan на полное удаление `.env`;
- цель в том, чтобы убрать `.env` из роли места, где оператор руками настраивает imported tools/functions после bootstrap.

### 4.3 Bootstrap policy

Bootstrap должен:

- делать idempotent upsert;
- переносить runtime settings в `Open WebUI`-native surfaces там, где это возможно;
- минимизировать post-bootstrap ручные edits;
- оставаться совместимым с drift API `Open WebUI`;
- уметь различать `secret/backend-owned values` и `native Open WebUI-managed values`;
- давать оператору drift-aware re-bootstrap path, а не blind overwrite.

### 4.4 Request override policy

Отдельно разрешаем только узкий слой `request-time overrides`, если они действительно нужны в UI:

- примерный allowlist: `temperature`, `max_tokens`, `top_p`, `seed`, `stop`;
- backend всё равно остаётся final authority для default/clamp/allowlist policy;
- low-level runtime knobs и backend prompts не становятся частью `Open WebUI` config даже если provider технически умеет принимать эти поля.
- retrieval/admin secrets для `Qdrant` и parser-service не становятся user-editable settings imported tools/functions.

## 5. 2-Day Execution Slices

### Day 1 — Responsibility split

Нужно сделать:

- зафиксировать четыре режима в docs/backlog;
- прекратить implicit backend tool routing для `plain model`;
- оставить `agent-navigator` только как explicit agent/compatibility profile;
- подтвердить, что canonical `Open WebUI` contour = `raw provider + native knowledge + explicit tools`.

Acceptance:

- обычный чат не вызывает backend tool route по умолчанию;
- explicit tool path не зависит от wrapper-mode;
- docs и backlog не описывают `agent-navigator` как default Open WebUI model.

Нормативные slices:

1. docs/backlog freeze:
   - `TASKS_MIGRATION.md`
   - `docs/plans/migration/2026-04-09-openwebui-responsibility-split-plan.md`
   - related migration plans
2. runtime boundary cleanup:
   - `backend/orchestrator/agent_api.py`
   - `backend/orchestrator/execution_runtime.py`
   - `backend/orchestrator/orchestration_runtime.py`
   - `backend/orchestrator/tool_execution.py`
   - targeted tests

Правило для Day 1:

- не смешивать boundary cleanup и catalog refactor в одном diff;
- не трогать больше `5` независимых файлов в одном execution slice.

### Day 2 — Native config + bootstrap

Нужно сделать:

- свести bootstrap к admin-managed setup `Open WebUI`;
- перенести tool/action config в native `Open WebUI` surfaces, где это возможно;
- убрать ручной drift между bootstrap script, imported code и `.env`-driven placeholder workflow;
- расширить export bundle так, чтобы он описывал не только code templates, но и ownership/runtime-config blocks;
- добавить drift-check между backend export bundle и materialized `Open WebUI` state;
- повторить smoke на internal tools и затем на одном community tool как secondary compatibility check.
- не переносить в этот slice backend-owned runtime/prompt config.

Acceptance:

- operator может переbootstrap’ить `Open WebUI` без ручной правки imported code;
- named tools/functions/prompts materialize’ятся предсказуемо;
- ownership граница `backend secrets` vs `Open WebUI native config` зафиксирована и проверяема;
- есть drift-check path для повторной синхронизации bootstrap state;
- `M3.7` не захватывает `UMS` runtime knobs, generation/RAG source-of-truth и `LangGraph`/tool prompts;
- есть plan/smoke path для одного community-tool compatibility check после стабилизации internal contour;
- `Qwen3` не считается частью acceptance текущего slice.

Нормативные slices:

1. bootstrap/config slice:
   - `scripts/bootstrap_openwebui.py`
   - `backend/orchestrator/tool_bindings.py`
   - export/control-plane docs
   - targeted tests
2. `Open WebUI` runtime verification slice:
   - live browser smoke
   - one community-tool compatibility check как secondary proof, что contour не завязан только на наши wrappers
   - docs/backlog update by fact, not by intent

## 6. Dependency Order

Нормативный порядок:

1. `M3.5` — named tools + live bootstrap smoke stabilization
2. `M3.6` — responsibility split (`plain model` / `native knowledge` / `explicit tools` / `agent mode`)
3. `M3.7` — `Open WebUI`-native config + bootstrap-managed settings
4. `M3.8` — `Open WebUI Knowledge + external ingestion + Qdrant` baseline
5. `M3.9` — финальное решение по роли `ask_document` (thin adapter vs compatibility-only)
6. `Unified Model Catalog / Gateway`

`M4.2` coexistence/sunset slice для `Chainlit` можно готовить параллельно как policy/doc task, но не использовать его как причину откладывать `M3.6/M3.7`.

## 7. Non-Goals

Этот plan не означает:

- мгновенное удаление `Chainlit` в том же diff;
- rewrite `UMS`;
- большой uncontrolled refactor всего `agent_api`;
- перенос бизнес-логики в `Open WebUI`;
- перенос backend-owned model runtime config или `LangGraph` prompt logic в `Open WebUI`;
- немедленное внедрение community tools в production path.

## 8. Success Criteria

Переход считается архитектурно успешным, если одновременно верны все пункты:

- `Open WebUI` может работать как понятный `model + tools` shell без backend-hidden routing;
- `Open WebUI` может работать как понятный `model + knowledge + tools` shell;
- `agent-navigator` существует только как explicit agent mode;
- tool config materialize’ится через bootstrap/native `Open WebUI` surfaces, а не через ручной post-import patching;
- backend secrets остаются backend-owned и не мигрируют в user-facing `Open WebUI` config;
- native Knowledge path не путается с explicit domain tools;
- community-tool smoke подтверждает, что native config contour не завязан только на наши imported wrappers;
- следующий cleanup slice по `UMS` model catalog делает только inventory/provider cleanup, а не исправляет responsibility split задним числом.
