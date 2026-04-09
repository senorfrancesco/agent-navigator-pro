# Open WebUI Named Tools and Bootstrap Plan

Status: execution-ready after current `P0` backend fixes.

> **2026-04-09 update:** этот plan остаётся execution plan для `M3.5`, но дальнейшая эволюция bootstrap/config теперь должна подчиняться [Open WebUI Responsibility Split Plan](/home/fisher/agent-navigator/docs/plans/migration/2026-04-09-openwebui-responsibility-split-plan.md): `raw model + explicit tools` как canonical contour, `agent-navigator` только как explicit agent mode, а runtime config tools/actions — по возможности в `Open WebUI`-native surfaces, а не в ручной `.env`-patching workflow.

## Summary

Для production/eval contour не нужно делать отдельный server на каждый инструмент. Каноническая модель остаётся такой:

- один backend-owned `OpenAPI Tool Server`;
- много backend endpoints-инструментов;
- `Open WebUI` показывает пользователю не server-level сущность, а явные named tools и follow-up actions.

Из живого smoke уже подтверждено:

- `OpenAPI Tool Server` в `Open WebUI` материализуется как источник инструментов, а не как каталог пользовательских задач;
- `Action Functions` реально появляются под сообщением и кликаются, если одновременно включены `is_active=true` и `is_global=true`;
- текущий UX server toggle сам по себе недостаточен для обычного пользователя, который не знает имён функций и не должен угадывать prompt.

Следовательно, целевой UX layer должен быть таким:

1. `Workspace > Tools` — primary user-facing слой с отдельными именованными Python wrappers.
2. `Action Functions` — secondary/follow-up слой для быстрых действий после ответа и для deep-job lifecycle.
3. `Prompts` — tertiary shortcut layer для power users.

## Canonical Decisions

### 1. Один backend server, много tools

Не делаем отдельный process/server на каждый tool. Это ухудшает:

- auth/config management;
- rollout/update path;
- observability;
- operational simplicity.

Правильная единица UX для пользователя — это named wrapper, а не отдельный backend process.

### 2. `Workspace > Tools` — основной explicit picker

Для явного выбора инструмента пользователем используем нативные Python tools в `Open WebUI Workspace`.

Это даёт ожидаемый UX:

- `Быстрый анализ оборудования`
- `Глубокий анализ оборудования`
- позже: document/compare tools

Пользователь видит задачу, а не transport layer.

### 3. `Action Functions` — follow-up, а не primary catalog

`Action Functions` нужны для:

- `Обновить deep-job`
- `Отменить deep-job`
- post-result follow-up actions

Они не заменяют named tools, а дополняют их.

### 4. `Prompts` — удобство, не канонический путь

`/hw-fast`, `/hw-deep` и аналогичные shortcuts сохраняются как convenience layer.

Они не считаются primary UX и не должны быть единственным способом запуска продуктовых сценариев.

### 5. Business logic остаётся в backend

Python wrappers в `Open WebUI` должны быть тонкими.

Они:

- собирают входные аргументы;
- валидируют минимальный UI context;
- вызывают backend tool endpoint через `httpx`;
- возвращают результат пользователю.

Они не должны переносить в `Open WebUI` routing policy, document lifecycle, retrieval logic или domain rules.

### 6. Bootstrap должен быть идемпотентным

API `Open WebUI` для tools/functions/prompts нужно считать integration surface с возможным drift между версиями. Поэтому bootstrap path должен:

- сначала читать текущее состояние;
- затем делать `upsert`, а не blind create;
- избегать дублей при повторном запуске;
- оставаться безопасным после обновлений `Open WebUI`.

### 7. Runtime config должен уходить в native `Open WebUI` surfaces

Bootstrap не должен навсегда оставлять imported tools/functions на placeholder-driven `.env` patching.

Нормативное направление такое:

- backend хранит secrets и export/control-plane metadata;
- `Open WebUI` хранит connection/tool/function/prompt state и те runtime valves/settings, которые поддерживает его текущая версия;
- non-secret user-facing defaults, labels, enable/disable state и другие UX-facing knobs должны materialize’иться именно в `Open WebUI`, а не оставаться ручной `.env` настройкой;
- operator должен иметь предсказуемый re-bootstrap path без ручного редактирования imported code после каждого drift/change.

Уточнение:

- это не означает, что все значения переезжают в `Open WebUI`;
- `backend/.env` остаётся местом для секретов, backend URLs и execution-policy значений, которые не должны жить в imported tool state.
- low-level serving/runtime knobs и backend prompts не входят в target native-config migration.

## Target Open WebUI Surfaces

### A. Tool Server

Оставляем один backend source:

- `Agent Navigator OpenAPI Tool Server`

Он нужен как transport layer и source-of-truth для endpoint contracts.

### B. Workspace Tools

Первая очередь:

- `Быстрый анализ оборудования`
- `Глубокий анализ оборудования`

Вторая очередь после `M2.1/M2.2`:

- `Спросить по документу`
- `Быстрый анализ документа`
- `Глубокий анализ документа`

Третья очередь после multi-document binding:

- `Сравнить документы (fast)`
- `Сравнить документы (deep)`

### C. Action Functions

Первый обязательный набор:

- `Быстрый анализ оборудования`
- `Глубокий анализ оборудования`
- `Обновить deep-job`
- `Отменить deep-job`

Отдельно зафиксирован runtime requirement:

- action button появляется в чате только если функция одновременно `is_active=true` и `is_global=true`.

### D. Prompts

Оставляем:

- `/hw-fast`
- `/hw-deep`

Они остаются вторичным способом запуска.

### E. Native runtime config

В `Open WebUI` нужно считать operator-managed state:

- tool server connection object;
- tool/function/prompt enablement;
- display metadata;
- valves/runtime defaults, если их поддерживает текущая версия `Open WebUI`.

В backend должны оставаться:

- auth tokens;
- backend base URLs/source-of-truth;
- export metadata и secret references;
- execution policy, не предназначенная для ручной UI-настройки;
- `UMS` / provider runtime параметры вроде `num_ctx`, `num_gpu`, `num_thread`, `use_mmap`, `use_mlock`, `keep_alive`;
- generation/parsing/RAG source-of-truth defaults;
- `LangGraph` prompts и tool/workflow internal prompts.

Отдельный узкий слой может позже быть разрешён как `request-time override` из UI, но только через backend allowlist/clamp, а не как новый source-of-truth.

## Wrapper Rules

### Equipment tools

Python wrapper делает прямой вызов в backend:

- `POST /tool-server/tools/analyze_equipment_fast`
- `POST /tool-server/tools/analyze_equipment_deep`

### Document tools

До завершения `M2.1/M2.2` document wrappers не должны считаться production-ready.

Когда они будут добавлены, wrapper обязан:

- проверить наличие выбранного документа или backend-owned document context;
- при отсутствии контекста вернуть понятное UX-сообщение;
- не проваливаться в backend error как primary user feedback.

Минимальный expected behavior:

`Сначала загрузите и выберите документ для этого инструмента.`

### Compare tools

Compare wrappers должны проверять наличие минимум двух документов в контексте.

При отсутствии двух документов wrapper возвращает понятное UX-сообщение, а не raw backend error.

Минимальный expected behavior:

`Для сравнения нужно выбрать минимум два документа.`

### Model binding

Named tools и action functions должны привязываться только к `raw.*` моделям.

`agent-navigator` wrapper path не должен быть primary execution model для этих flows.

Если модель выбирает tool сама, это допустимо только внутри native tool-calling contour `Open WebUI`, когда tools уже явно переданы в chat runtime.

## Deep Job UX Contract

Нельзя считать auto-polling `Open WebUI` гарантированным baseline.

Поэтому usable UX для deep jobs должен опираться на:

- accepted response с `job_id`;
- сохранённый `status_url`;
- follow-up action `Обновить deep-job`;
- follow-up action `Отменить deep-job`.

Требование к реализации:

- deep wrapper или action должны сохранить `job_id`/`status_url` в таком виде, чтобы refresh/cancel могли восстановить их из message context без prompt hacks.

## Bootstrap Automation

Рекомендуемый delivery path:

- backend export bundle как source-of-truth;
- отдельный idempotent bootstrap script, например `scripts/bootstrap_openwebui.py`.

Bootstrap должен уметь:

1. Проверять доступность `Open WebUI`.
2. Читать текущие registered tool servers.
3. Читать текущие tools/functions/prompts.
4. Делать `upsert` tool server.
5. Делать `upsert` named Python tools.
6. Делать `upsert` action functions.
7. Делать `upsert` prompts.
8. Включать для action functions нужные flags.
9. Печатать итоговый отчёт без дублей.
10. Показывать ownership/drift summary: что остаётся backend-owned, а что materialize’ится в `Open WebUI`.

Источники для bootstrap:

- backend export endpoint;
- `backend/.env` как bootstrap input / backend-side source-of-truth;
- admin token или admin credentials `Open WebUI`.

Нормативное уточнение:

- `.env` не должен оставаться долгосрочным user-facing config surface для imported tools;
- bootstrap должен переносить runtime settings в `Open WebUI` connection / valves / admin-managed state там, где это поддерживает текущая версия `Open WebUI`;
- export bundle должен описывать не только code templates, но и native-config blocks / default valves / ownership hints для bootstrap;
- нужен drift-check path между export bundle и живым `Open WebUI` state, чтобы re-bootstrap не был blind overwrite;
- ручной post-import edit допустим только как временный workaround, а не как canonical delivery path.
- после стабилизации internal tools нужен минимум один follow-up smoke с community tool как compatibility harness: он проверяет не “новую фичу”, а то, что модели и native config contour работают не только с нашими wrappers.
- этот compatibility harness должен отдельно прогоняться в `Native Function Calling` режиме минимум на `Qwen2.5` и `Qwen3`, с фиксацией различий по tool invocation quality/result.
- `M3.7/P4` не являются планом по переносу operator runtime panels, `UMS` tuning и backend prompts в `Open WebUI`.

## Execution Phases

### P0 — Stabilize current blockers

Обязательные блокеры перед named-tools rollout:

1. Исправить backend bug в `analyze_equipment_fast`.
2. Исправить передачу deep-job context для `refresh/cancel`.

Без этого нельзя честно валидировать UX layer.

### P1 — Named Equipment Tools

Внедрить первые Python tools во `Workspace > Tools`:

- `Быстрый анализ оборудования`
- `Глубокий анализ оборудования`

Acceptance:

- пользователь видит именованный инструмент, а не только server entry;
- инструмент вызывает backend endpoint без переноса бизнес-логики в wrapper.

### P2 — Follow-up Actions

Оставить и стабилизировать action layer:

- `Обновить deep-job`
- `Отменить deep-job`

Acceptance:

- deep-job flow usable без native auto-polling;
- action state не теряется из-за отсутствия сохранённого `status_url`.

### P3 — Idempotent Bootstrap

Сделать автоматический bootstrap:

- tool server;
- named tools;
- action functions;
- prompts.

### P4 — Native Config Follow-up

После стабилизации `M3.5` bootstrap должен эволюционировать дальше:

- минимизировать hardcoded runtime settings внутри imported code;
- использовать `Open WebUI`-native config surfaces для tool/action runtime settings;
- расширить export bundle ownership/runtime-config metadata;
- добавить drift-check/report перед и после re-bootstrap;
- дать operator-friendly re-bootstrap path без ручного blind patching;
- зафиксировать отдельный compatibility smoke для community tools после стабилизации internal tools;
- прогнать минимум по одной community tool-конфигурации на `Qwen2.5` и `Qwen3` в `Native Function Calling` режиме и сохранить comparative notes.

Acceptance:

- повторный запуск не создаёт дубли;
- обновление существующих записей предсказуемо;
- поведение не зависит от ручного кликанья в админке;
- ownership граница `backend secret` / `Open WebUI native config` явно описана и проверяема;
- backend-owned runtime/prompt config не мигрирует в `Open WebUI` этим slice;
- есть сравнительный smoke-result по community tool для `Qwen2.5` и `Qwen3`.

### P5 — Document and Compare Tools

После `M2.1/M2.2`:

- добавить document wrappers;
- добавить compare wrappers;
- enforce context checks на стороне wrapper UX.

## Verification

### Backend verification

- targeted `pytest` по equipment/document tool routes;
- `python -m py_compile` по затронутым backend modules;
- `git diff --check`

### Open WebUI verification

- named tools видны во вкладке `Workspace > Tools`;
- user может включить конкретный инструмент по названию;
- `equipment fast` работает end-to-end;
- `equipment deep` работает end-to-end;
- `refresh/cancel` работают без prompt hacks;
- document wrappers без context дают понятное UX-сообщение.
- есть хотя бы один compatibility smoke с community tool после стабилизации internal tools/native config.
- comparative notes для `Qwen2.5` и `Qwen3` фиксируют, как каждая модель ведёт себя в `Native Function Calling` с community tool.
- community tool используется именно как проверка native tool/model compatibility, а не как перенос продуктовой логики во внешний tool ecosystem.

## Operational Notes

- `OpenAPI Tool Server` не удаляется; он остаётся transport layer и backend contract surface.
- `Workspace > Tools` поверх него — это UX adaptation layer, а не перенос логики в Open WebUI.
- `Action Functions` уже доказали жизнеспособность как real clickable UI, но их нужно считать secondary layer.
- Вкладка server-level tools в compose bar не решает задачу explicit tool picking для обычного пользователя.

## Sources

- OpenAPI Tool Servers:
  <https://docs.openwebui.com/features/plugin/tools/openapi-servers/open-webui/>
- Tools:
  <https://docs.openwebui.com/features/extensibility/plugin/tools/>
- Action Functions:
  <https://docs.openwebui.com/features/extensibility/plugin/functions/action/>
- Workspace Prompts:
  <https://docs.openwebui.com/features/workspace/prompts>
