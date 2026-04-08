# TASKS_MIGRATION - Agent Navigator Pro

> **Primary operational backlog for migration scope.** Этот файл является каноническим source-of-truth для `Open WebUI` migration, backend tool contracts, upload/document binding, knowledge-base prerequisites и rollout-критериев. `TASKS.md` остаётся общепроектным backlog и хранит только краткие cross-project ссылки и follow-up.

> **Plan library:** все migration-related планы теперь собраны в [docs/plans/migration/README.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/README.md).

## Scope

### In scope

- `Open WebUI` как controlled migration/evaluation contour
- `OpenAPI Tool Server` integration path
- `MCP` как совместимый слой поверх backend contracts
- backend-first tool contracts вместо перегруженного `/orchestrate`
- upload/document binding и lifecycle документов
- KB/Qdrant prerequisites в той мере, в какой они блокируют migration
- cleanup legacy `Open WebUI` runtime/docs path

### Out of scope

- unrelated `offline_bundle` / operator UI work
- независимые `compare` / `equipment` улучшения без прямой связи с migration path
- общий release backlog, не меняющий migration contour
- ранний rename `backend/open_webui_uploads` без стабилизированного upload contract

## Current Migration Status (2026-04-07)

- Текущий canonical UI проекта: `Chainlit`
- `Open WebUI` в репозитории живёт как `legacy` docker profile на порту `3001`
- `agent_api.py` уже даёт OpenAI-compatible surface и request dedup для Open WebUI, но это compatibility/eval path, а не финальный product contract
- `backend/open_webui_uploads` фактически стал shared storage для обоих UI и report/export flows; это compatibility name, но живой storage contract
- Уже закрыто в migration contour:
  - `M0.1`, `M0.2`, `M0.3`
  - `M1.1`, `M1.2`, `M1.3`
  - `M3.2`
  - `M4.0`
- Сейчас в активном progress:
  - `M3.1` — compose contour / pinned image / runtime smoke
  - `M3.3` — реальный `Open WebUI` smoke через `User Tool Server`
  - `M3.4` — `model/tool split` для `Open WebUI native tool-calling`
- Самый устаревший слой migration path сейчас находится в:
  - `scripts/run_openwebui.sh`
  - частях docs, которые смешивают target vision, legacy path и текущее состояние
  - master-plan, который описывает `Open WebUI-first` как установленный факт, хотя repo policy пока фиксирует `Chainlit-first`

## Canonical Decisions

- `Open WebUI` возвращаем как **controlled evaluation contour**, а не как немедленный `main UI switch`
- До functional parity `Chainlit` остаётся canonical UI и debug shell
- Основной integration path: `OpenAPI Tool Server`
- `MCP` поддерживается как второй слой, но не как стартовый production path
- Бизнес-логика и orchestration policy остаются во внешнем backend, не в `Open WebUI`
- `backend/open_webui_uploads` сохраняется как текущее shared storage имя до отдельного approved rename slice
- Migration идёт фазами; один implementation slice по умолчанию затрагивает не более `5` файлов
- Long-running jobs, ingestion и reindex не проектируются как “магический streaming tool call”; backend остаётся source-of-truth для job state

## Future Architecture Directions (not current scope)

- `Open WebUI` как orchestration layer:
  в будущем `Open WebUI` может использоваться как внешний orchestration shell через native workflows / pipelines поверх уже готовых backend tool contracts. Это не меняет текущую модель `user as orchestrator`, а только требует, чтобы backend tools оставались stable и frontend-neutral до подключения любого внешнего orchestrator.
- `Classifier-assisted routing`:
  отдельный слой поверх tool catalog, который выбирает `requested_tool` и `routing_mode` без переноса domain logic в UI. Это не MVP и не входит в `M1`; сначала нужен стабильный explicit tool contract.

## Active Migration Phases

### M0 — Foundation / Cleanup

- [x] M0.1 — Создать `TASKS_MIGRATION.md` как отдельный migration ledger
  Решение: migration backlog больше не ведём разрозненно между `TASKS.md` и `docs/plans/*`.
  Verification: `git diff --check`
- [x] M0.2 — Сверить canonical docs и policy
  Контекст:
  - `AGENTS.md` и `README.md` фиксируют `Chainlit` как основной UI;
  - migration master-plan описывает `Open WebUI` как primary direction без transitional wording.
  Нужно сделать:
  - переписать migration docs на controlled-eval framing;
  - явно развести current state и target direction;
  - не объявлять миграцию завершённой заранее.
  Acceptance:
  - `README.md`, migration docs и backlog не конфликтуют между собой;
  - implementer не должен гадать, какой UI canonical сегодня.
  Progress:
  - выделен целевой wording: `Chainlit-first today`, `Open WebUI controlled eval next`;
  - master-plan, `README.md` и script runtime docs синхронизированы без объявления premature main-UI switch.
  Verification:
  - `git diff --check`
- [x] M0.3 — Убрать legacy mixed-runtime illusion вокруг `scripts/run_openwebui.sh`
  Контекст:
  - скрипт поднимает старый mixed path через `tmux` и Docker;
  - это противоречит текущему launcher/runtime contract.
  Нужно сделать:
  - превратить скрипт в узкий compatibility wrapper;
  - убрать видимость “главного entrypoint”;
  - сохранить только ручной recovery/eval сценарий.
  Acceptance:
  - `run_openwebui.sh` не стартует отдельный shadow runtime;
  - docs больше не рекламируют его как основной путь.
  Done:
  - `run_openwebui.sh` переведён в compose-only compatibility helper;
  - legacy assertions в tests обновлены под новый contract.
  Verification:
  - `bash -n scripts/run_openwebui.sh`
  - `pytest backend/tests/test_scripts_help.py backend/tests/test_runtime_launcher.py -q`

### M1 — Backend Tool Contract

- [x] M1.1 — Спроектировать публичный backend-first contract для явных tools
  Контекст:
  - текущий `OrchestrationRequest` перегружен control-plane полями;
  - для `Open WebUI` нужен узкий и предсказуемый tool catalog.
  Целевой набор:
  - `ask_document`
  - `analyze_document_fast`
  - `analyze_document_deep`
  - `compare_documents_fast`
  - `compare_documents_deep`
  - `analyze_equipment_fast`
  - `analyze_equipment_deep`
  Acceptance:
  - у каждого tool есть request/response schema;
  - `fast/deep` является частью tool contract, а не UI profile.
  Plan:
  - [OpenAPI Tool Server MVP Implementation Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-07-openapi-tool-server-mvp-plan.md)
  Done:
  - введены `backend/orchestrator/tool_catalog.py` и `backend/orchestrator/tool_schemas.py` как preparatory source-of-truth для names, execution modes и request/response contracts;
  - текущий slice не меняет transport layer и не переписывает `OrchestrationRequest`, а только нормализует backend-owned tool contract.
  - explicit tool contract начал подключаться к текущему backend execution path через отдельный dispatch-layer, который адаптирует `requested_tool` к существующему `forced_route`, не создавая второй router.
- [x] M1.2 — Ввести `requested_tool` / `routing_mode` как frontend-neutral orchestration contract
  Источник: бывший `B3.52` из `TASKS.md`
  Acceptance:
  - backend умеет исполнять явный выбор действия без classifier-first routing;
  - контракт одинаково применим к `Chainlit`, `Open WebUI` и будущему custom frontend.
  Done:
  - `requested_tool` и `routing_mode` уже вынесены в новый schema layer как independent contract, который потом может быть использован и в OpenAPI endpoints, и в compatibility surfaces.
  - `agent_api.py` начал принимать `requested_tool` / `routing_mode` в текущем request model и пробрасывать их в execution payload как deterministic explicit-tool contract.
- [x] M1.3 — Нормализовать модель длинных задач
  Нужно сделать:
  - определить `job_id` / `status_url` / `result_ref` contract;
  - отделить quick tools от heavy jobs;
  - не завязывать ingestion/reindex на один длинный UI call.
  Done:
  - process-local registry заменён на persistent backend-owned tool job store, использующий тот же DB root, что и orchestration state layer;
  - deep tools теперь пишут durable `queued/running/cancelling/completed/failed/cancelled` state и сохраняют terminal result/error в persistent ledger;
  - добавлены restart semantics: orphaned `queued/running/cancelling` jobs при startup честно переводятся в `failed` с `error_summary=interrupted:process-restart`;
  - добавлен публичный `cancel` contract для `/tool-jobs/{job_id}/cancel` и `/tool-server/tool-jobs/{job_id}/cancel`;
  - result contract нормализован: `404` для unknown job, `409` для not-ready и terminal-no-result path.
  Verification:
  - `pytest backend/tests/test_tool_job_store.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_openapi_tools_api.py backend/tests/test_tool_contracts.py -q`
  - `python -m py_compile backend/orchestrator/tool_job_store.py backend/orchestrator/tool_execution.py backend/orchestrator/agent_api.py backend/orchestrator/openapi_tools_api.py backend/orchestrator/tool_schemas.py`

### M2 — Upload / Document Binding / KB Prerequisites

- [ ] M2.1 — Ввести backend upload contract
  Нужно сделать:
  - принимать файлы как backend-owned entities;
  - присваивать `file_id`, `document_id`, `version_id`, `thread_id`;
  - убрать передачу по системе “голых локальных путей” как основного интерфейса.
- [ ] M2.2 — Ввести document binding model
  Нужно сделать:
  - хранить `thread -> document_version` binding;
  - отделить session overlay от persistent KB;
  - определить lifecycle для uploads, reports и follow-up reuse.
- [ ] M2.3 — Подготовить KB abstraction path под migration
  Нужно сделать:
  - завершить `KnowledgeBaseStoreProtocol`;
  - не привязывать `Open WebUI` contour напрямую к текущей SQLite-реализации;
  - отдельно подготовить `Qdrant` phase как storage replacement, а не UI rewrite.
- [ ] M2.5 — Вынести document parsing / OCR / table extraction во внешний ingestion contour
  Нужно сделать:
  - определить канонический parser-service path (`Docling` first, `Tika` optional fallback);
  - не делать `Open WebUI Knowledge` source-of-truth для production parsing;
  - разделить parsing, embeddings и vector-store lifecycle от chat UI.
- [ ] M2.6 — Подготовить corpus admin UI как backend-owned control plane
  Нужно сделать:
  - добавить в operator/admin surface загрузку документов, reindex, delete, verify, collection management;
  - считать `Open WebUI Knowledge` максимум eval/helper surface, а не canonical corpus admin;
  - зафиксировать versioning, dedup, audit и access rules на стороне backend/admin UI.
- [ ] M2.4 — Не делать ранний rename `backend/open_webui_uploads`
  Статус: explicit defer
  Решение:
  - rename возможен только после стабилизации upload/document binding и path mapping;
  - до этого каталог считается shared storage contract, несмотря на legacy-имя.

### M3 — Open WebUI Eval Integration

- [ ] M3.1 — Подготовить актуальный `Open WebUI` compose contour
  Нужно сделать:
  - уйти от плавающего `ghcr.io/open-webui/open-webui:main` к pin на проверенный release;
  - добавить явный `WEBUI_SECRET_KEY`;
  - сохранить native Open WebUI RAG выключенным на первом этапе.
  Acceptance:
  - legacy profile остаётся изолированным eval contour;
  - startup не зависит от старого mixed-runtime script path.
  Progress:
  - `open-webui` переведён на `backend/.env` через `env_file`, чтобы bootstrap/admin policy задавались из канонического env source, как и у `Chainlit`;
  - добавлены `WEBUI_SECRET_KEY`, `WEBUI_ADMIN_EMAIL`, `WEBUI_ADMIN_PASSWORD`, `WEBUI_ADMIN_NAME`, `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE`;
  - legacy profile сохранён отдельным;
  - native RAG остаётся выключенным.
  Remaining:
  - зафиксировать стабильный pinned image tag после нормального pull/smoke вместо временного `latest`;
  - отдельный runtime smoke с реальным `docker compose --profile legacy up -d open-webui`.
- [x] M3.2 — Подключить backend как `OpenAPI Tool Server`
  Нужно сделать:
  - зарегистрировать backend tools как явный tool server;
  - не переносить бизнес-логику в `Functions`;
  - отдельно определить auth/CORS/path mapping.
  Done:
  - backend получил отдельный `tool-only` OpenAPI surface с `/tools/*`, `/tool-jobs/*` и выделенным `/tool-server/openapi.json`;
  - для tool server введён `Static Bearer` baseline через `OPENAPI_TOOL_SERVER_TOKEN`;
  - transport layer остаётся backend-owned adapter поверх текущего explicit-tool execution path, без переноса domain logic в `Open WebUI`;
  - `document_refs` временно принимают `session_file_ref` и `file_path` как eval-only fallback до завершения `M2.1`;
  - CORS defaults сужены до localhost dev/eval origins вместо wildcard.
- [ ] M3.3 — Собрать controlled smoke path
  Пользовательский сценарий:
  - загрузить файл
  - выбрать tool
  - выполнить tool
  - получить grounded answer или report
  Acceptance:
  - нет hidden second-brain behavior со стороны `Open WebUI`;
  - backend остаётся source-of-truth для routing и document lifecycle.
  Progress:
  - `Open WebUI` login/bootstrap подтверждён на `legacy` profile;
  - реальный UI path для `User Tool Server` найден в `Settings -> Integrations -> Add Connection`;
  - подтверждено, что Open WebUI ожидает в поле `URL` base path (`/tool-server`), а не полный spec URL, и сам добавляет `/openapi.json`.
  - backend `tool-server` surface расширен `GET /tool-server/api/config` и prefixed alias routes `/tool-server/tools/*`, `/tool-server/tool-jobs/*`, чтобы legacy Open WebUI мог materialize’ить tool server без поломки canonical root paths в schema.
  - route-aware async contract подтверждён: prefixed `analyze_equipment_fast` даёт `200`, prefixed `analyze_equipment_deep` даёт `202` с `status_url=/tool-server/tool-jobs/...`.
  - browser-side connection check теперь подтверждён end-to-end: после выравнивания Bearer token `User Tool Server` проходит `Проверить подключение`, materialize’ится в чате как `Agent Navigator OpenAPI Tool Server` и становится доступным через `Available Tools`.
  - дополнительно подтверждено, что materialized tool server ещё нужно явно включить для конкретного чата через compose-bar integration button (`доступные инструменты` / switch `Agent Navigator OpenAPI Tool Server`); пока switch выключен, raw provider честно отвечает, что не имеет доступа к tool, и backend не получает `/tool-server/tools/*`.
  - non-document fast tool подтверждён end-to-end в реальном чате: `Open WebUI -> POST /api/chat/completions -> POST /tool-server/tools/analyze_equipment_fast -> completed response`, результат отображается в чате со source card `analyze_equipment_fast`.
  - non-document deep tool подтверждён на transport/runtime уровне: `Open WebUI -> POST /tool-server/tools/analyze_equipment_deep -> 202 accepted`, accepted payload с `job_id` и `status_url` корректно виден в source card `analyze_equipment_deep`, а backend job завершается через persistent `tool-jobs` contract.
  Blockers:
  - browser-side connection check не проходит, если backend недоступен по browser-reachable `:8000`;
  - текущий native runtime по умолчанию поднимает `tool-server` с fallback token `agent-navigator-tool-server-dev-token`, если `OPENAPI_TOOL_SERVER_TOKEN` не задан в `backend/.env`; из-за этого сохранённый в UI токен легко расходится с runtime token и даёт ложный `401 tool-server-auth-required` до ручного выравнивания.
  - для dockerized `Open WebUI` проявился host/container split: browser-side verify видит native backend через `127.0.0.1`, а server-side refresh/import внутри контейнера материализует server entry только через `host.docker.internal` / bridge-reachable host URL;
  - deep-tool UX в `Open WebUI` пока не закрыт end-to-end: accepted response показывается пользователю как текст/источник с `job_id` и `status_url`, но автоматического polling `GET /tool-server/tool-jobs/{job_id}` со стороны UI пока не подтверждено; финальный completed result backend уже умеет отдавать, но `Open WebUI` не дочитывает его автоматически в текущем contour.
  - fast/deep tool invocation на raw provider теперь работает только при явном chat-level enable tool server; это нужно считать частью supported smoke instructions, иначе пользователь получает ложный вывод “модель не умеет tools”.
  - container smoke через `run_all.sh` / `launcher.sh --target container` теперь intentionally идёт через `--no-build`, поэтому при отсутствии локальных образов `agent-api` / `ums` сначала нужен отдельный explicit build step;
  - document-tool smoke остаётся зависимым от `M2.1/M2.2`, даже если non-document tools уже готовы.
  Guide:
  - [Open WebUI Eval Contour](/home/seral/HDD/proj/agent-navigator-pro/docs/guides/openwebui-eval-contour.md)
  - [Open WebUI Model / Tool Split Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-model-tool-split-plan.md)

- [x] M3.4 — Развести model provider и tool server в `Open WebUI`
  Контекст:
  - практический smoke подтвердил, что один только `OpenAPI Tool Server` не решает integration UX, если выбранная chat model уже является `agent-navigator` wrapper;
  - Open WebUI docs рассматривают providers/models и tool servers как разные integration surfaces;
  - native function calling зависит от качества выбранной модели и её реальной tool-calling поддержки.
  Progress:
  - provider inventory закрыт на уровне кода: текущий внешний `/v1/chat/completions` в `agent_api` остаётся product wrapper path, а `UMS` публикует `/infer`, `/models`, `/status`, `/v1/embeddings`, но не готовый raw `/v1/chat/completions`;
  - для этого добавлен thin raw-chat gateway в `agent_api`: `GET /raw/v1/models` и `POST /raw/v1/chat/completions`;
  - raw gateway идёт напрямую в `UMS`, не строит `OrchestrationRequest`, не injects `assistant_mode`/`runtime_mode` и не тянет backend orchestration policy в Open WebUI provider path;
  - non-stream raw path уже покрыт targeted tests как protocol-clean OpenAI-compatible response;
  - stream raw path отделён от text-only `UMSClient.async_infer_stream` и идёт прозрачным proxy на `UMS /infer`, чтобы не терять upstream SSE contract.
  - live smoke подтвердил, что `Open WebUI` видит новый provider как `raw.*` model set; после выбора `raw.qwen-14b-llm` backend перестаёт получать `POST /v1/chat/completions` и начинает принимать `POST /raw/v1/chat/completions` от контейнера `open-webui`.
  - простой chat-level smoke на `raw.qwen-14b-llm` прошёл без product-wrapper текста: модель ответила напрямую, а backend log зафиксировал raw provider path вместо legacy wrapper path.
  - живой smoke на topology `raw model + tool server` подтверждён после явного включения `Agent Navigator OpenAPI Tool Server` в текущем чате: `analyze_equipment_fast` и `analyze_equipment_deep` больше не идут через product wrapper path и вызываются как реальные `/tool-server/tools/*` calls.
  Acceptance:
  - fast tool response больше не проходит через product-specific assistant wrapper;
  - deep tool accepted/result path проверяется уже без `double wrapping`;
  - `agent-navigator` остаётся optional specialized assistant / compatibility mode, а не primary Open WebUI provider.
  Done:
  - raw-provider smoke повторён на `raw.qwen-14b-llm` с отдельно включённым `Agent Navigator OpenAPI Tool Server`;
  - backend log подтвердил split маршрутов: обычный чат идёт в `POST /raw/v1/chat/completions`, fast tool идёт в `POST /tool-server/tools/analyze_equipment_fast`, deep tool идёт в `POST /tool-server/tools/analyze_equipment_deep -> 202 Accepted`;
  - оставшийся хвост по auto-polling deep jobs отнесён к `M3.3` как UX limitation `Open WebUI`, а не как дефект model/tool split.
  Plan:
  - [Open WebUI Model / Tool Split Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-model-tool-split-plan.md)
- [ ] M3.5 — Ввести named tools и bootstrap automation для `Open WebUI`
  Контекст:
  - живой smoke подтвердил, что server-level tool picker в compose bar показывает источник инструментов, а не понятный каталог пользовательских задач;
  - `Action Functions` уже подтверждены как живой clickable UX, но это follow-up слой, а не основной каталог инструментов;
  - для обычного пользователя primary explicit picker должен жить во вкладке `Workspace > Tools` как именованные thin Python wrappers поверх backend tool server.
  Нужно сделать:
  - завести `Workspace > Tools` wrappers для `equipment` tools как первый production-like UX slice;
  - сохранить `Action Functions` для deep-job refresh/cancel и других follow-up действий;
  - оставить `Prompts` только как convenience layer;
  - автоматизировать настройку `Open WebUI` через idempotent bootstrap path без дублей.
  Acceptance:
  - пользователь видит named tools по конкретным задачам, а не только server entry;
  - thin Python wrappers не содержат business logic и вызывают единый backend `OpenAPI Tool Server`;
  - bootstrap path делает `upsert`, а не blind create, и не плодит дубли в `Open WebUI`;
  - document/compare wrappers при отсутствии нужного document context возвращают понятное UX-сообщение, а не raw backend error.
  Progress:
  - `P0` blockers закрыты:
    - `analyze_equipment_fast` больше не деградирует в compare/document-only error path без пары документов и теперь уходит в equipment-specific assistant fallback;
    - deep-job action layer начал возвращать structured `tool_job` context (`job_id`, `status_url`), а `refresh/cancel` templates восстанавливают его не только по regex-тексту, но и из nested message payload.
  - backend export bundle расширен `workspaceTools` для `equipment_fast_tool` и `equipment_deep_tool` как thin Python wrappers для `Workspace > Tools`;
  - action templates получили canonical `isActive=true` / `isGlobal=true` flags в export bundle, чтобы bootstrap не зависел от ручного post-import toggle;
  - добавлен `scripts/bootstrap_openwebui.py` как idempotent admin bootstrap:
    - читает `/operator/tool-bindings/export/openwebui`;
    - логинится в `Open WebUI` через `/api/v1/auths/signin`;
    - делает `upsert` для `tool server`, `Workspace > Tools`, `Action Functions` и `Prompts` через admin API без blind create и без дублей по canonical identity.
  Blockers:
  - [x] backend bug в `analyze_equipment_fast` исправлен как обязательный `P0` gate перед rollout named tools;
  - [x] deep-job context стабилизирован так, чтобы `refresh/cancel` actions могли восстанавливать `status_url` / `job_id` из message context.
  Remaining:
  - живой bootstrap smoke против реального `Open WebUI` container/admin state ещё не прогнан после добавления `scripts/bootstrap_openwebui.py`;
  - нужно подтвердить, что `Workspace > Tools` entries после bootstrap действительно видны пользователю как named tools и не конфликтуют с existing `Action Functions` / `Prompts` contour.
  Зависимости / порядок:
  - `M3.5` должен быть закрыт до начала structural slice по unified `Model Catalog / Gateway`;
  - причина: `M3.5` даёт immediate user-facing value и меньше риск конфликтов в `agent_api` / provider-tool integration surface.
  Plan:
  - [Open WebUI Named Tools and Bootstrap Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-named-tools-bootstrap-plan.md)

### M4 — Hardening / Coexistence / Rollout

- [x] M4.0 — Ввести минимальный backend hardening для operator surface на время migration
  Контекст:
  - operator UI уже используется как admin/runtime panel;
  - полноценного внешнего auth/ACL слоя для него пока нет.
  Done:
  - backend-served `operator` surface (`/operator`, `/operator-ui`, `/operator-assets`) закрыт для non-loopback clients;
  - введён `OPERATOR_UI_LOCALHOST_ONLY=true` как default policy в `backend/.env` / `.env.example`;
  - этот флаг и `Open WebUI` bootstrap/access knobs выведены в operator `Secrets / Access`.
  Verification:
  - `pytest backend/tests/test_operator_config_service.py backend/tests/test_operator_ui_api.py -q`
  - `python -m py_compile backend/orchestrator/operator_config_service.py backend/orchestrator/operator_ui_api.py backend/orchestrator/agent_api.py`
- [ ] M4.1 — Добавить `MCP` как совместимый слой поверх готовых backend contracts
  Acceptance:
  - `MCP` не заменяет `OpenAPI-first` path;
  - prompt/slash scenarios опираются на тот же backend tool catalog.
  Plan:
  - [Open WebUI MCP / OpenAPI / Actions Architecture Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-07-openwebui-mcp-tools-actions-architecture-plan.md)
- [ ] M4.x — Unified Model Catalog / Gateway
  Контекст:
  - public raw model list и runtime inventory сейчас расходятся между `agent_api` и `UMS`;
  - это cleanup/consistency slice, а не immediate blocker для текущего tool flow.
  Precondition:
  - `M3.5 — Open WebUI Named Tools + Bootstrap` закрыт и eval contour устойчив.
  Plan:
  - [Unified Model Catalog / Gateway Implementation Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-unified-model-catalog-gateway-plan.md)
- [ ] M4.2 — Определить coexistence contract для `Chainlit` и `Open WebUI`
  Нужно сделать:
  - зафиксировать роль `Chainlit` как debug/dev shell;
  - определить критерии, когда `Open WebUI` может стать primary UI;
  - исключить drift в docs, scripts и launch surface.
- [ ] M4.3 — Подготовить migration exit criteria
  Минимум:
  - functional parity по обязательным user flows;
  - стабильный upload/document binding;
  - tool catalog доступен из `Open WebUI`;
  - regression/smoke checks описаны и воспроизводимы.
- [ ] M4.4 — Добавить admin UI для registry внешних tool servers
  Нужно сделать:
  - ввести backend-owned registry для `OpenAPI` / `MCP` tool servers;
  - дать operator/admin UI для add/edit/disable/scope/auth policy;
  - разделить `our core tools` и `external utility tools`;
  - не делать `Open WebUI` единственным местом, где живёт конфигурация внешних integrations.
  Progress:
  - добавлен backend-owned static binding catalog для tool UX layer: `direct_action`, `prompt_shortcut`, disabled document/compare bindings с явными dependency flags;
  - `operator` API теперь отдаёт `/operator/tool-bindings`, `/operator/tool-actions/catalog` и `/operator/tool-bindings/export/openwebui` как control-plane foundation для будущих Action Functions / Workspace Prompts;
  - `analyze_equipment_fast` и `analyze_equipment_deep` начали возвращать canonical `available_actions` payload с `binding_id`, чтобы follow-up actions больше не строились ad hoc в UI.
  - export bundle для `/operator/tool-bindings/export/openwebui` расширен до manual-bootstrap контракта:
    - `browserReachableBaseUrl` и `containerReachableBaseUrl` для tool server;
    - import-ready `Workspace Prompts` `/hw_fast` и `/hw_deep`;
    - 4 import-ready `Action Functions` templates для `equipment` flows и deep-job refresh/cancel;
    - `importChecklist` для ручного admin bootstrap в `Open WebUI`.
  Remaining:
  - persistence/editing для bindings и registry внешних tool servers;
  - подтверждённый manual import/smoke layer для Open WebUI `Action Functions` и `Workspace Prompts`;
  - actual import/deployment automation layer для Open WebUI `Action Functions`;
  - operator UI rendering для этих bindings, а не только API surface.

## Open Tasks / Priorities

1. M3.3 — завершить controlled smoke через `Open WebUI` `User Tool Server` на native backend path
2. M3.5 — ввести named tools и idempotent bootstrap automation для `Open WebUI`
3. M3.1 — довести `Open WebUI` compose contour до pinned-tag + стабильного runtime smoke
4. M2.1 / M2.2 — определить upload/document binding как backend-owned model
5. Unified Model Catalog / Gateway — только после закрытия `M3.5` и стабилизации eval contour
6. M2.5 / M2.6 — вынести parsing/corpus admin в backend-owned ingestion + admin UI contour
7. M4.4 — довести tool UX control plane до persistent operator-managed registry и wiring в Open WebUI actions/prompts
8. M4.1 / M4.2 — определить coexistence contract и MCP layer

## Risks / Blockers / Workarounds

- Риск: drift между `TASKS.md`, `README.md`, `AGENTS.md` и migration docs.
  Митигатор: все migration decisions сначала фиксировать здесь, затем обновлять docs в том же slice.
- Риск: случайно сломать текущий `Chainlit` path под видом cleanup старого `Open WebUI`.
  Митигатор: не переименовывать shared uploads path и не трогать backend compatibility hooks без explicit acceptance.
- Риск: `Open WebUI` станет вторым decision engine через native RAG / functions / hidden routing.
  Митигатор: `RAG off`, `OpenAPI-first`, business logic only in backend.
- Workaround: пока tool-server contract не готов, можно использовать OpenAI-compatible path только как временный eval contour.
- Workaround: `backend/open_webui_uploads` остаётся compatibility-name, даже если фактически обслуживает оба UI.
- Workaround: первый `accepted job` contract для deep tools опирается на process-local background registry; для shared/multi-process rollout это нужно будет заменить на backend-owned persistent job/result store.
- Workaround: до отдельного proxy/policy slice `legacy Open WebUI` smoke нужно считать dual-URL контуром: browser-side backend probes идут на `127.0.0.1`, а container-side refresh/import может требовать `host.docker.internal` или другой host-reachable адрес, общий и для браузера, и для контейнера.

## Verification Contract

- Для docs/backlog slices:
  - `git diff --check`
- Для shell/runtime cleanup:
  - `bash -n scripts/run_openwebui.sh`
  - targeted script tests при затронутом launcher/help behavior
- Для Python/backend contract slices:
  - `python -m py_compile` по затронутым модулям
  - targeted `pytest`
- Нельзя объявлять migration step завершённым без project-aware verification по затронутому слою.

## Working Notes

### 2026-04-07 — Initial migration ledger seeding

- Решение: `TASKS_MIGRATION.md` становится primary operational backlog для migration scope.
- Решение: `TASKS.md` остаётся общепроектным backlog и хранит только краткую migration-ссылку и cross-project references.
- Решение: current default framing — `Chainlit-first today`, `Open WebUI controlled eval next`, не `Open WebUI-first immediately`.
- Решение: rename `backend/open_webui_uploads` откладывается до отдельного approved slice.
- Progress: для `M0.2` зафиксирован docs narrative, в котором `Open WebUI` больше не должен описываться как уже включённый primary UI до достижения parity.
- Progress: добавлен отдельный guide для `Open WebUI` eval contour и отдельный MVP-plan для `OpenAPI Tool Server`, чтобы runtime/docs cleanup и backend contracts не смешивались в одном документе.
- Progress: compose-only contract для `run_openwebui.sh` подтверждён таргетными tests (`68 passed` для `test_scripts_help.py` + `test_runtime_launcher.py`).
- Workaround: для локального recovery/bootstrap `Open WebUI` contour зафиксирован headless admin bootstrap через `WEBUI_ADMIN_*`; целевая signup policy после первичной инициализации — `ENABLE_SIGNUP=True` и `DEFAULT_USER_ROLE=pending`.
- Progress: начат `M1` preparatory slice — добавлены backend-owned tool catalog и canonical request/response schemas без изменения transport/API wiring.
- Внешние опоры для migration direction:
  - Open WebUI OpenAPI servers: <https://docs.openwebui.com/openapi-servers/>
  - Open WebUI OpenAPI integration: <https://docs.openwebui.com/features/plugin/tools/openapi-servers/open-webui/>
  - Open WebUI MCP docs: <https://docs.openwebui.com/features/mcp>
  - Open WebUI RAG docs: <https://docs.openwebui.com/features/rag>
  - Releases: <https://github.com/open-webui/open-webui/releases>

## Done / Closed Items

- [x] Создан отдельный migration ledger вместо продолжения migration work внутри общего `TASKS.md`
- [x] Зафиксировано, что detailed migration tasks `B3.52` / `B3.53` / `B3.54` переехали в этот файл как `M1.2`, coexistence-related follow-up и `M3.*`
- [x] Docs и policy reconciled под `Chainlit-first today / Open WebUI controlled eval next`
- [x] `scripts/run_openwebui.sh` больше не поднимает shadow runtime и остаётся compose-only compatibility helper
- [x] `Open WebUI` bootstrap/admin policy вынесена в `backend/.env` contract и показана в operator `Secrets / Access`
- [x] Operator surface временно закрыт backend-level `localhost-only` guard до отдельной auth/ACL phase
