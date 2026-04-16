# TASKS_MIGRATION - llm-tools-platform

> **Primary operational backlog for migration scope.** Этот файл является каноническим source-of-truth для `Open WebUI` migration, backend tool contracts, upload/document binding, knowledge-base prerequisites и rollout-критериев. `TASKS.md` остаётся общепроектным backlog и хранит только краткие cross-project ссылки и follow-up.

> **Plan library:** все migration-related планы теперь собраны в [docs/plans/migration/README.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/README.md).

## Scope

### In scope

- `Open WebUI` как primary UI / user shell
- `OpenAPI Tool Server` integration path
- `MCP` как совместимый слой поверх backend contracts
- backend-first tool contracts вместо перегруженного `/orchestrate`
- upload/document binding и lifecycle документов
- `Open WebUI Knowledge` / `Qdrant` / retrieval source-of-truth
- внешний ingestion/parsing contour (`Docling` first, `Tika` fallback)
- KB/Qdrant prerequisites и corpus admin в той мере, в какой они блокируют migration
- cleanup legacy `Open WebUI` runtime/docs path

### Out of scope

- unrelated `offline_bundle` / operator UI work
- независимые `compare` / `equipment` улучшения без прямой связи с migration path
- общий release backlog, не меняющий migration contour
- ранний rename `backend/open_webui_uploads` без стабилизированного upload contract

## Current Migration Status (2026-04-10)

- Текущее canonical target direction проекта: `Open WebUI-first`
- `Open WebUI` в репозитории пока всё ещё поднимается как `legacy` docker profile на порту `3001`, но это уже implementation debt, а не product decision
- `Chainlit` больше не считается target UI; он остаётся только временным compatibility/debug shell до отдельного sunset slice
- `agent_api.py` уже даёт raw-provider surface, `OpenAPI Tool Server` surface и compatibility `llm-tools-platform` path; дальше нужно не спорить о роли UI, а довести `Knowledge + Qdrant + explicit tools` как согласованный target contour
- `backend/open_webui_uploads` фактически стал shared storage для обоих UI и report/export flows; это compatibility name, но живой storage contract
- Уже закрыто в migration contour:
  - `M0.1`, `M0.2`, `M0.3`
  - `M1.1`, `M1.2`, `M1.3`
  - `M3.2`
  - `M3.5`
  - `M3.6`
  - `M4.0`
- Сейчас в активном progress:
  - `M3.1` — compose contour / pinned image / runtime smoke
  - `M3.3` — реальный `Open WebUI` smoke через `User Tool Server`
  - `M3.7` — `Open WebUI`-native config/bootstrap
  - следующий продуктовый слой после `M3.7`: `M3.8` (`Open WebUI Knowledge + external ingestion + Qdrant`) и `M3.9` (окончательная роль `ask_document`)
- Самый устаревший слой migration path сейчас находится в:
  - частях `TASKS_MIGRATION.md`, которые ещё говорят `controlled eval` / `Chainlit-first`
  - document-tool contour, где `ask_document` и native `Open WebUI Knowledge` пока не разведены до конца
  - backend document binding для `/tools/*`, который всё ещё не готов как production path для document/compare tools
  - ingestion/admin contour, который ещё не материализован как canonical source-of-truth для корпуса

## Canonical Decisions

- `Open WebUI` считаем canonical primary UI и user-facing shell
- `Chainlit` оставляем только как временный compatibility/debug shell до sunset slice
- Основной integration path: `OpenAPI Tool Server`
- `MCP` поддерживается как второй слой, но не как стартовый production path
- Обычный document QA / knowledge chat должен жить нативно в `Open WebUI Knowledge`
- `Qdrant` считаем canonical vector backend для Knowledge / RAG
- production parsing / OCR / table extraction выносятся во внешний ingestion contour (`Docling` first, `Tika` fallback)
- Бизнес-логика и orchestration policy остаются во внешнем backend, не в `Open WebUI`
- Для `Open WebUI` вводим четыре явных режима:
  - `plain model` — backend не принимает tool decision;
  - `native knowledge` — обычный document/KB chat через `Open WebUI Knowledge`;
  - `explicit tools` — backend исполняет только явно выбранный tool; модель может выбирать только из тех tools, которые уже переданы ей нативным contour `Open WebUI`;
  - `agent mode` — classifier/orchestration разрешены только как explicit opt-in profile
- explicit backend tools считаем canonical только для specialized workflows:
  - `analyze_document_fast/deep`
  - `compare_documents_fast/deep`
  - `analyze_equipment_fast/deep`
- роль `ask_document` остаётся отдельным decision point:
  - либо thin adapter над тем же retrieval source-of-truth, что и `Open WebUI Knowledge`;
  - либо compatibility-only tool вне основного UX
- ВАЖНО: document QA и document analysis не проектируются как "один канонический deep-отчёт на документ".
  Нужно жёстко разделять:
  - состояние документа:
    - `document_id` / `version_id`;
    - извлечённый текст;
    - corpus metadata;
    - индекс и фильтры retrieval в `Qdrant`;
  - состояние конкретного запуска анализа:
    - `question` или `analysis_goal`;
    - `job_id`;
    - артефакт ответа / отчёт;
    - статус и история конкретного запуска.
- ВАЖНО: follow-up по тому же документу должен переиспользовать не "старый готовый deep-ответ", а тот же `document_ref` и retrieval source-of-truth.
  Нормативная семантика:
  - `ask_document(document_ref, question=...)` — точечный вопрос по документу;
  - `analyze_document_deep(document_ref, analysis_goal=...)` — новый целевой запуск анализа под конкретную задачу.
- ВАЖНО: `Qdrant` — retrieval-слой и source-of-truth для knowledge/document search, а не подмена состояния инструментов.
  История чата может помогать модели, но не должна быть единственным источником follow-up ответа по документу.
- `backend/open_webui_uploads` сохраняется как текущее shared storage имя до отдельного approved rename slice
- Migration идёт фазами; один implementation slice по умолчанию затрагивает не более `5` файлов
- Long-running jobs, ingestion и reindex не проектируются как “магический streaming tool call”; backend остаётся source-of-truth для job state
- `backend/.env` остаётся backend-side source-of-truth и bootstrap input, но не должен оставаться долгосрочным user-facing config path для imported `Open WebUI` tools/actions
- corpus metadata / dedup / versioning / audit не должны жить только в `Open WebUI` state; нужен backend/admin-owned corpus contract
- после стабилизации internal tools нужен хотя бы один compatibility smoke с community tool, чтобы `Open WebUI`-native config/bootstrap не были завязаны только на наш export bundle

## Future Architecture Directions (not current scope)

- `Open WebUI` как orchestration layer:
  `Open WebUI` уже рассматривается как основной shell; future direction здесь — возможные native workflows / pipelines поверх готовых backend tool contracts без возврата hidden backend routing в обычный чат.
- `Classifier-assisted routing`:
  отдельный слой поверх tool catalog, который выбирает `requested_tool` и `routing_mode` без переноса domain logic в UI. Это не MVP и не входит в `M1`; сначала нужен стабильный explicit tool contract.
- `Session docs in Qdrant`:
  в `V1` session overlay допускается вне `Qdrant`; в `V2` можно переносить short-lived session docs в `Qdrant` с filtering по `thread_id` / `workspace_id`.

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
- [x] M2.2 — Ввести document binding model
  Статус: `backend` теперь хранит постоянный реестр `thread -> document_version`, формирует канонические привязки в `agent_api`, поддерживает жизненный цикл `session`-документов через `expires_at` и очищает просроченные session-привязки вместе с индексированными чанками.
  Нужно сделать:
  - хранить `thread -> document_version` binding;
  - отделить session overlay от persistent KB;
  - определить lifecycle для uploads, reports и follow-up reuse.
- [ ] M2.3 — Подготовить KB abstraction path под migration
  Статус: `KnowledgeBaseStoreProtocol`, store-backed поиск для `session/knowledge`, жизненный цикл `delete_chunks*` и развёртывание `QdrantKnowledgeBaseStore` для workflow в `backend` уже реализованы; отдельно остаётся только нативное подключение `Open WebUI Knowledge` к тому же source-of-truth.
  Нужно сделать:
  - завершить `KnowledgeBaseStoreProtocol`;
  - не привязывать `Open WebUI Knowledge` contour напрямую к текущей SQLite-реализации;
  - подготовить `Qdrant` как canonical vector backend за protocol boundary;
  - сохранить единый retrieval source-of-truth для native Knowledge path и backend workflows.
- [ ] M2.5 — Вынести document parsing / OCR / table extraction во внешний ingestion contour
  Нужно сделать:
  - определить канонический parser-service path (`Docling` first, `Tika` optional fallback);
  - не делать `Open WebUI` UI-процесс source-of-truth для production parsing;
  - разделить parsing, embeddings и vector-store lifecycle от chat UI;
  - определить handoff между `Open WebUI` upload/Knowledge UX и внешним ingestion pipeline.
- [ ] M2.6 — Подготовить corpus admin UI как backend-owned control plane
  Нужно сделать:
  - добавить в operator/admin surface загрузку документов, reindex, delete, verify, collection management;
  - считать `Open WebUI Knowledge` user-facing retrieval shell, но не canonical corpus admin;
  - зафиксировать versioning, dedup, audit и access rules на стороне backend/admin UI.
- [ ] M2.4 — Не делать ранний rename `backend/open_webui_uploads`
  Статус: explicit defer
  Решение:
  - rename возможен только после стабилизации upload/document binding и path mapping;
  - до этого каталог считается shared storage contract, несмотря на legacy-имя.

### M3 — Open WebUI Primary UI Integration

- [x] M3.1 — Подготовить актуальный `Open WebUI` compose contour
  Нужно сделать:
  - уйти от плавающего `ghcr.io/open-webui/open-webui:main` к pin на проверенный release;
  - добавить явный `WEBUI_SECRET_KEY`;
  - развести backend `session RAG` и native `Open WebUI Knowledge` на одном `Qdrant` без смешивания коллекций.
  Acceptance:
  - основной `Open WebUI` compose contour поднимается без отдельного `legacy` profile;
  - startup не зависит от старого mixed-runtime script path;
  - `Open WebUI` и backend используют один сервер `Qdrant`, но разные пространства коллекций.
  Done:
  - `open-webui` переведён на `backend/.env` через `env_file`, чтобы bootstrap/admin policy задавались из канонического env source, как и у `Chainlit`;
  - добавлены `WEBUI_SECRET_KEY`, `WEBUI_ADMIN_EMAIL`, `WEBUI_ADMIN_PASSWORD`, `WEBUI_ADMIN_NAME`, `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE`;
  - `legacy` profile для основного `Open WebUI` runtime убран; основной стек теперь является базовым compose-контуром;
  - `open-webui` закреплён на `ghcr.io/open-webui/open-webui:0.8.12`;
  - в `docker-compose.yaml` добавлен `qdrant` и runtime-настройки `Open WebUI` для `VECTOR_DB=qdrant`, multitenancy и внешних эмбеддингов через `UMS`;
  - backend `agent-api` в контейнерном контуре использует `QDRANT_URL=http://qdrant:6333`;
  - `scripts/run_all.sh` и `./scripts/launcher.sh --target container` теперь поднимают `Open WebUI` + backend + `Qdrant`; `Chainlit` в этом контуре больше не стартует по умолчанию;
  - `scripts/run_native.sh`, `scripts/stop_native.sh` и `./scripts/launcher.sh --target native` теперь поднимают host-side backend вместе с `Qdrant`, а `Open WebUI` запускают как primary UI; `--skip-openwebui` становится каноническим флагом, а `--skip-chainlit` остаётся только совместимым алиасом;
  - подготовлено операторское руководство по bootstrap и ручной настройке `Knowledge`/`session` на одном `Qdrant`.
  Verification 2026-04-13:
  - `docker compose config`
  - `git diff --check -- docker-compose.yaml backend/.env.example docs/deploy-guide.md docs/guides/openwebui-qdrant-operator-guide.md`
  Note:
  - canonical `Knowledge + external ingestion + Qdrant` product contour всё ещё остаётся в `M3.8`; здесь закрыт именно инфраструктурный `compose/bootstrap/operator` слой.
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
  - browser-side connection check теперь подтверждён end-to-end: после выравнивания Bearer token `User Tool Server` проходит `Проверить подключение`, materialize’ится в чате как `llm-tools-platform OpenAPI Tool Server` и становится доступным через `Available Tools`.
  - дополнительно подтверждено, что materialized tool server ещё нужно явно включить для конкретного чата через compose-bar integration button (`доступные инструменты` / switch `llm-tools-platform OpenAPI Tool Server`); пока switch выключен, raw provider честно отвечает, что не имеет доступа к tool, и backend не получает `/tool-server/tools/*`.
  - non-document fast tool подтверждён end-to-end в реальном чате: `Open WebUI -> POST /api/chat/completions -> POST /tool-server/tools/analyze_equipment_fast -> completed response`, результат отображается в чате со source card `analyze_equipment_fast`.
  - non-document deep tool подтверждён на transport/runtime уровне: `Open WebUI -> POST /tool-server/tools/analyze_equipment_deep -> 202 accepted`, accepted payload с `job_id` и `status_url` корректно виден в source card `analyze_equipment_deep`, а backend job завершается через persistent `tool-jobs` contract.
  Blockers:
  - browser-side connection check не проходит, если backend недоступен по browser-reachable `:8000`;
  - текущий native runtime по умолчанию поднимает `tool-server` с fallback token `llm-tools-platform-tool-server-dev-token`, если `OPENAPI_TOOL_SERVER_TOKEN` не задан в `backend/.env`; из-за этого сохранённый в UI токен легко расходится с runtime token и даёт ложный `401 tool-server-auth-required` до ручного выравнивания.
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
  - практический smoke подтвердил, что один только `OpenAPI Tool Server` не решает integration UX, если выбранная chat model уже является `llm-tools-platform` wrapper;
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
  - живой smoke на topology `raw model + tool server` подтверждён после явного включения `llm-tools-platform OpenAPI Tool Server` в текущем чате: `analyze_equipment_fast` и `analyze_equipment_deep` больше не идут через product wrapper path и вызываются как реальные `/tool-server/tools/*` calls.
  Acceptance:
  - fast tool response больше не проходит через product-specific assistant wrapper;
  - deep tool accepted/result path проверяется уже без `double wrapping`;
  - `llm-tools-platform` остаётся optional specialized assistant / compatibility mode, а не primary Open WebUI provider.
  Done:
  - raw-provider smoke повторён на `raw.qwen-14b-llm` с отдельно включённым `llm-tools-platform OpenAPI Tool Server`;
  - backend log подтвердил split маршрутов: обычный чат идёт в `POST /raw/v1/chat/completions`, fast tool идёт в `POST /tool-server/tools/analyze_equipment_fast`, deep tool идёт в `POST /tool-server/tools/analyze_equipment_deep -> 202 Accepted`;
  - оставшийся хвост по auto-polling deep jobs отнесён к `M3.3` как UX limitation `Open WebUI`, а не как дефект model/tool split.
  Plan:
  - [Open WebUI Model / Tool Split Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-model-tool-split-plan.md)
- [x] M3.5 — Ввести named tools и bootstrap automation для `Open WebUI`
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
  - live bootstrap smoke против реального `Open WebUI` container/admin state прогнан:
    - первый live прогон вскрыл два реальных bootstrap defects, исправленных в том же slice:
      - prompt lookup через `GET /api/v1/prompts/command/*` в текущем `Open WebUI` отдавал HTML SPA fallback вместо JSON; bootstrap переведён на list-based prompt discovery (`/api/v1/prompts/list` с fallback на `/api/v1/prompts/`);
      - imported `Workspace > Tools` / `Action Functions` оставались с placeholder `SET_OPENAPI_TOOL_SERVER_TOKEN`, из-за чего live execution падал в `401`; bootstrap теперь inject’ит реальный `OPENAPI_TOOL_SERVER_TOKEN` при upsert.
    - повторный live bootstrap после фиксов проходит успешно и materialize’ит:
      - `llm-tools-platform OpenAPI Tool Server` connection;
      - `Workspace > Tools`: `equipment_fast_tool`, `equipment_deep_tool`;
      - `Action Functions`: `equipment_fast_action`, `equipment_deep_action`, `tool_job_refresh_action`, `tool_job_cancel_action`;
      - `Prompts`: `/hw_fast`, `/hw_deep`.
  - browser-side smoke подтверждён в legacy `Open WebUI`:
    - `Workspace > Tools` показывает оба named tools как отдельные записи;
    - `Workspace > Prompts` показывает `/hw_fast` и `/hw_deep` без поломки prompts contour;
    - action functions остаются активными/global по admin API и доступны в chat UI как follow-up buttons;
    - user-level tools menu показывает named tools рядом с server-level sources, то есть пользователь видит не только provider entry, но и конкретные equipment tools.
  - execution smoke подтверждён частично на живом contour:
    - после token injection клик по `Быстрый анализ оборудования` даёт реальный `POST /tool-server/tools/analyze_equipment_fast -> 200 OK`;
    - клик по `Глубокий анализ оборудования` даёт реальный `POST /tool-server/tools/analyze_equipment_deep -> 202 Accepted`;
    - для clean chat path дополнительно подтверждено, что bootstrap больше не ломает обычный `llm-tools-platform` wrapper response после подъёма `UMS`.
  - 2026-04-10 headed browser smoke (`Open WebUI` в видимом Chrome) дал более точную картину user-visible contour:
    - clean login стартует с `raw.qwen-14b-llm`, named tools доступны в tool picker, но при `Function Calling = Default` даже явная инструкция “используй инструмент” остаётся обычным `POST /raw/v1/chat/completions` без backend tool call;
    - после ручного перевода `Controls > Function Calling` в `Native` тот же `raw.qwen-14b-llm` начинает реально вызывать named tools из chat UX;
    - explicit fast prompt через visible chat дал `POST /tool-server/tools/analyze_equipment_fast -> 200 OK` и уже materialize’ился в assistant bubble как нормальный user-visible ответ;
    - follow-up click по `Глубокий анализ оборудования` дал `POST /tool-server/tools/analyze_equipment_deep -> 202 Accepted`, а `Обновить deep-job` дал `GET /tool-server/tool-jobs/{job} -> 200 OK`;
    - `Отменить deep-job` дошёл как минимум до `POST /api/chat/actions/tool_job_cancel_action` внутри `Open WebUI`, но в этом прогоне не дал подтверждённого backend `POST /tool-server/tool-jobs/{job}/cancel`.
    - deep contour параллельно запускает raw chat stream и при занятом `UMS` ловит `429` на `/infer`, из-за чего `agent_api` пишет `502 raw-model-stream-failed` при уже стартовавшем deep job; UI оставляет status-history bubble (`Запускаю analyze_equipment_deep...`) и follow-up actions, но это ещё не clean UX.
  - 2026-04-10 cleanup pass закрыл найденный drift в bootstrap/config contour:
    - local `backend/.env` приведён к token contract из `.env.example`, после рестарта bootstrap больше не уходит на dev fallback;
    - old fallback bearer больше не проходит execution route (`401`), а example token проходит schema/config/execution checks;
    - bootstrap удаляет legacy prompt commands `/hw-fast` / `/hw-deep`, повторный bootstrap идемпотентен (`legacyPromptCleanupCount=0`);
    - bootstrap теперь materialize’ит `DEFAULT_MODEL_PARAMS.function_calling=native` вместе с `raw.qwen-14b-llm`; live bootstrap summary подтверждает `defaultFunctionCalling=native`, targeted tests покрывают preserve existing params и no-op path;
    - tool-server connection merge нормализует `url` + `path` и схлопывает старую shape `.../tool-server` + `openapi.json` в canonical `http://host.docker.internal:8000` + `/tool-server/openapi.json`;
    - OpenAPI schema browser probe больше не требует bearer token, но execution/status/cancel routes остаются bearer-protected.
  - 2026-04-10 closure pass добил live action-state contour без патча `Open WebUI` image:
    - deep action больше не пишет transient `Запускаю ...` как source-of-truth; persisted `statusHistory` materialize’ится structured accepted entry с `status`, `job_id`, `status_url`, `tool_name`;
    - `tool_job_refresh_action` и `tool_job_cancel_action` теперь восстанавливают job context не только из nested payload/status text, но и через live-shape fallback `chat_id + message_id -> open_webui.models.chats.Chats.get_chat_by_id(...)`;
    - при нескольких deep runs extractor теперь берёт последний `statusHistory` entry (`latest-wins`), а `cancel` больше не зависит от сломанного `__event_call__` confirm-step;
    - headed Chrome rerun на живом contour подтвердил backend routes end-to-end:
      - `GET /tool-server/tool-jobs/870d81dd-80b0-4714-82ed-993d85d127b4 -> 200 OK`;
      - `GET /tool-server/tool-jobs/870d81dd-80b0-4714-82ed-993d85d127b4/result -> 200 OK`;
      - fresh deep run дал `POST /tool-server/tools/analyze_equipment_deep -> 202 Accepted`, а follow-up cancel дал `POST /tool-server/tool-jobs/4166a156-ffc9-41ae-8951-6f4717549214/cancel -> 200 OK`.
  - 2026-04-12 tool-catalog hardening:
    - backend tool catalog расширен до user/model-facing contract (`label`, `when to use`, `input/output summary`, `enabled/deferred`);
    - export bundle и `/tool-server/api/config` теперь явно публикуют enabled subset (`analyze_equipment_fast/deep`) и deferred subset (`ask_document`, document/compare tools);
    - OpenAPI descriptions для `/tools/*` синхронизированы с canonical catalog, чтобы в `Open WebUI` и schema probe не торчали transport-level формулировки вместо product intent.
    - default bootstrap contour переведён на `named-tools-only`: `bootstrap_openwebui.py` удаляет canonical `llm-tools-platform OpenAPI Tool Server` из live picker state и чистит `community_sum_tool` как verification fixture, оставляя bootstrap-managed `equipment_*` named tools.
  Blockers:
  - [x] backend bug в `analyze_equipment_fast` исправлен как обязательный `P0` gate перед rollout named tools;
  - [x] deep-job context materialize’ится в persisted `Open WebUI` message state так, чтобы `refresh/cancel` actions могли восстанавливать `status_url` / `job_id` без ручного ввода.
  Final verification:
  - bootstrap держит `raw.qwen-14b-llm` + `DEFAULT_MODEL_PARAMS.function_calling=native`, поэтому headed smoke больше не требует ручного toggle `Controls > Function Calling`;
  - visible contour подтверждает named tools, fast `200`, deep `202`, refresh `GET /tool-jobs/{job}`, cancel `POST /tool-jobs/{job}/cancel` на живом `Open WebUI`;
  - runtime smoke по-прежнему зависит от поднятого native `UMS :8090`, но это уже инфраструктурная предпосылка contour, а не remaining blocker для `M3.5`.
  Зависимости / порядок:
  - `M3.5` должен быть закрыт до начала structural slice по unified `Model Catalog / Gateway`;
  - причина: `M3.5` даёт immediate user-facing value и меньше риск конфликтов в `agent_api` / provider-tool integration surface.
  Plan:
  - [Open WebUI Named Tools and Bootstrap Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-named-tools-bootstrap-plan.md)
- [x] M3.6 — Развести `plain model`, `native knowledge`, `explicit tools` и `agent mode`
  Контекст:
  - текущий `llm-tools-platform` wrapper всё ещё пересекается с `Open WebUI` tool UX и может скрыто выбирать backend tools в обычном chat path;
  - для слабых машин canonical path должен быть проще: `raw/live model` плюс явно подключённые tools, без постоянной agentic orchestration;
  - `Open WebUI` хотим сделать target primary UI, а `Chainlit` — временным compatibility shell до sunset slice.
  Нужно сделать:
  - зафиксировать четыре режима как явные runtime contours;
  - запретить implicit backend tool routing для `plain model`;
  - оставить model-side tool choice только внутри нативного `Open WebUI` tool-calling contour, когда tools уже явно переданы модели;
  - оставить agentic decision только в отдельном `agent mode`;
  - сделать `raw provider + explicit tools` canonical `Open WebUI` contour.
  Acceptance:
  - обычный `Open WebUI` chat не вызывает backend tools автоматически;
  - native Knowledge path не смешивается с explicit backend tools;
  - `/tool-server/tools/*` выполняет только явно выбранный tool;
  - `llm-tools-platform` больше не описывается как default Open WebUI model для tool workflows;
  - docs/backlog последовательно разводят model path, native knowledge, tool path и agent mode.
  Progress:
  - Slice 1 runtime boundary cleanup закрыт commit `54db4a3`:
    - explicit tool contract теперь принудительно ставит `execution_surface=explicit_tool` и `runtime_mode=specialized_tasks` даже если входной payload просит `chat_only`;
    - `/orchestrate`, `/execute_orchestration` и OpenAI-compatible `/v1/chat/completions` проставляют явный `execution_surface`;
    - runtime classifier больше не резолвится для `chat_only`, `explicit_tool`, `compat_chat`, `forced_route` и уже готового `classifier_result`.
  - Slice 2 factual contour verification прогнан 2026-04-10 на live legacy `Open WebUI` + native backend contour:
    - `./scripts/run_native.sh --skip-chainlit --no-attach` поднял `agent_api :8000`, `UMS :8090`, document/legal services; `/health` для `agent_api` и `UMS` вернул `ok`;
    - `scripts/bootstrap_openwebui.py --backend-base-url http://localhost:8000 --openwebui-base-url http://127.0.0.1:3001` прошёл успешно и materialize’ил `workspaceToolCount=2`, `actionFunctionCount=4`, `promptCount=2`, `legacyPromptCleanupCount=0`, `defaultModel=raw.qwen-14b-llm`;
    - direct raw probe `POST /raw/v1/chat/completions` на `qwen-14b-llm` вернул `200 OK`, backend log зафиксировал только `POST /raw/v1/chat/completions`, без `/v1` wrapper и без `/tool-server`;
    - explicit fast probe `POST /tool-server/tools/analyze_equipment_fast` вернул `status=completed`, `routing_mode=explicit`, `execution_mode=sync`, `runtime_mode=specialized_tasks`;
    - explicit deep probe `POST /tool-server/tools/analyze_equipment_deep` вернул `202 Accepted`, затем `GET /tool-server/tool-jobs/{job}` дошёл до `completed`, а `GET .../result` вернул содержательный результат с `runtime_mode=specialized_tasks`;
    - compatibility probe `POST /v1/chat/completions` на `llm-tools-platform` вернул `200 OK` и остался на explicit agent/compatibility endpoint;
    - Playwright smoke подтвердил login в `Open WebUI`, `Workspace > Tools` показывает 2 named equipment tools, `Workspace > Prompts` показывает только canonical `/hw_fast` / `/hw_deep`, backend log показывает запросы контейнера к `/tool-server/openapi.json`, `/raw/v1/models` и `/v1/models`;
    - clean user/session path после local storage reset стартует с selected model `raw.qwen-14b-llm`, а `llm-tools-platform` остаётся opt-in compatibility model;
    - browser console после reload показывает `0` errors; остались только legacy warnings по manifest enctype и duplicate tiptap extensions, не связанные с contour routing.
  - Verification 2026-04-10:
    - targeted contour/unit suite: `91 passed`;
    - full backend non-integration suite: `903 passed, 5 deselected, 2 warnings`;
    - `python -m py_compile` по изменённым Python-файлам и `git diff --check` проходят.
  Notes:
  - первый deep-job результат в параллельном probe вернул backend `busy` из-за занятого LLM слота; retry после освобождения слота прошёл успешно, поэтому это capacity/serialization observation, а не routing regression.
  Plan:
  - [Open WebUI Responsibility Split Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-09-openwebui-responsibility-split-plan.md)
- [x] M3.7 — Перенести runtime config инструментов в `Open WebUI`-native bootstrap/config contour
  Контекст:
  - текущий bootstrap уже умеет materialize’ить tool server, tools, functions и prompts, но долгосрочно не хочется держать imported tools на ручной `.env`-логике и ad-hoc patching;
  - `Open WebUI` уже имеет native surfaces для connections, tools, functions, prompts и valves/admin-managed state.
  Нужно сделать:
  - определить ownership matrix: какие значения остаются backend-side secrets/source-of-truth, а какие должны жить как `Open WebUI`-native config;
  - расширить bootstrap так, чтобы operator мог переbootstrap’ить `Open WebUI` без ручной правки imported code;
  - использовать `Open WebUI` connections / valves / admin-managed state как основной runtime-config layer там, где это поддерживается версией;
  - расширить export bundle ownership/runtime-config metadata и добавить drift-check между export и materialized `Open WebUI` state;
  - зафиксировать smoke path для native config и один follow-up compatibility smoke с community tool как проверку contour вне нашего bundle;
  Не входит в этот slice:
  - перенос `UMS` / provider runtime knobs (`num_ctx`, `num_gpu`, `num_thread`, `use_mmap`, `use_mlock`, `keep_alive` и т.п.) в `Open WebUI`;
  - перенос generation/parsing/RAG source-of-truth в `Open WebUI`;
  - перенос `LangGraph` prompts и tool/workflow internal prompts в `Open WebUI`.
  Acceptance:
  - imported tools/actions не требуют ручного post-import редактирования для базового runtime;
  - bootstrap/admin path управляет runtime-настройкой предсказуемо и идемпотентно;
  - `.env` остаётся bootstrap input и backend secret source-of-truth, но не user-facing config surface для долгой жизни contour;
  - ownership граница `backend secrets` / `Open WebUI native config` явно описана и проверяема;
  - backend-owned runtime/prompt config остаётся вне `Open WebUI` migration contour;
  - community tool smoke описан как secondary compatibility check после стабилизации internal tools, а не как новый primary integration path;
  - `Qwen3` не считается частью acceptance текущего slice.
  Verification 2026-04-11:
  - Slice 1 внедрён: export bundle теперь включает `runtimeConfig` и `ownership`, а bootstrap summary materialize’ит `driftSummary` по connections/tools/functions/prompts/default model;
  - re-bootstrap сохраняет `Open WebUI`-owned поля (`config.enable`, user-edited labels/descriptions, `is_active`, `is_global`) и чинит только backend-owned drift;
  - live bootstrap после перезапуска `agent-api` подтвердил `runtimeConfig.defaultModel=raw.qwen-14b-llm`, `runtimeConfig.defaultFunctionCalling=native` и непустой ownership matrix;
  - targeted verification: `40 passed`, `python -m py_compile`, `git diff --check`;
  - на конец Slice 1 оставались два целевых добора: secondary community-tool compatibility smoke и более строгий no-op на `target_models`/materialized state; оба добраны в последующих Slice 2/3.
  Verification 2026-04-11 (Slice 2):
  - live `Open WebUI` materialized state не сохраняет `meta.manifest.target_models` для imported tools/functions; bootstrap теперь трактует это как benign `materializationDriftIgnored`, а не как perpetual update;
  - double-bootstrap на живом `Open WebUI` теперь даёт `driftSummary.noOp=true`, пустые `updatedIds` для tools/functions и явный `materializationDriftIgnored` по `meta.manifest.target_models`;
  - targeted verification обновлённого slice: `43 passed`, `python -m py_compile`, `git diff --check`;
  Verification 2026-04-11 (Slice 3):
  - добавлены verification-only harness files `tests/harness/openwebui/openwebui_community_sum_tool.py` и `tests/harness/openwebui/manage_openwebui_community_tool.py`; через admin API подтверждены install/status/delete lifecycle для внешнего `Workspace > Tool` без включения fixture в bootstrap-managed bundle;
  - coexistence с bootstrap подтверждён: при установленном `community_sum_tool` повторный `python scripts/bootstrap_openwebui.py` остаётся `driftSummary.noOp=true`, managed resources не обновляются, fixture tool остаётся нетронутым и затем удаляется cleanly;
  - visible `Open WebUI` smoke на `raw.qwen-14b-llm` подтвердил, что запросы на внешний tool реально уходят через native contour, а не через наш wrapper path: `POST /api/chat/completions` содержит `tool_ids=[\"community_sum_tool\"]`, а после явной установки `Controls > Вызов функции = Нативно` второй запрос уходит с `params.function_calling=\"native\"`;
  - во время community smoke backend не получает `/tool-server/tools/*`, то есть внешний proof действительно отделён от llm-tools-platform tool-server path;
  - позднее в том же контуре подтверждён exact result `COMMUNITY_TOOL_OK:18`; раннее ощущение “пустого” ответа оказалось latency effect из-за CPU-backed `raw.qwen-14b-llm`, а не интеграционным дефектом native community-tool path.
  - после этого added authoring slice: появился канонический guide `docs/openwebui-workspace-tools.md`, reusable template `scripts/templates/openwebui_workspace_tool_template.py`, generic helper `scripts/manage_openwebui_tool.py` и repo-level Codex skill `.agents/skills/openwebui-workspace-tools`; `tests/harness/openwebui/manage_openwebui_community_tool.py` оставлен как thin wrapper/example для smoke fixture.
  - добавлен отдельный diagnostic harness `tests/harness/openwebui/openwebui_followup_payload_harness.py`, который в чистом temporary chat captures first/second `POST /api/chat/completions` и пишет классификацию `state_bug` / `contamination` / `tool_selection_or_runtime` / `success` по request payload и видимому deterministic result;
  Verification 2026-04-13:
  - export bundle расширен блоками `runtimeConfig.rag`, `knowledgeConfig`, `qdrantConfig`, `manualChecklist`, `preflightRequirements`;
  - `bootstrap_openwebui.py` получил `preflight`, `warnings`, `knowledgeBootstrapMode` и режим `--dry-run`;
  - добавлен операторский guide для ручной настройки `Open WebUI` + `Qdrant`;
  - targeted verification: `pytest backend/tests/test_operator_ui_api.py backend/tests/test_openwebui_bootstrap.py -q`, `python -m py_compile backend/orchestrator/tool_bindings.py scripts/bootstrap_openwebui.py backend/tests/test_operator_ui_api.py backend/tests/test_openwebui_bootstrap.py`.
  - live payload capture в изолированном single-tool чате подтвердил `success`: и первый, и второй `POST /api/chat/completions` ушли с `tool_ids=["community_sum_tool"]`, `params.function_calling="native"`, а второй ход снова вызвал `sum_two_numbers` и вернул `14`;
  - следовательно, предыдущий follow-up сбой был не `Open WebUI state-loss`, а contamination/debug-run artifact: если в каталоге доступны посторонние tools, модель может уйти в них, но при изоляции одного tool persistence второго хода работает корректно.
  Plan:
  - [Open WebUI Responsibility Split Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-09-openwebui-responsibility-split-plan.md)
  - [Open WebUI Named Tools and Bootstrap Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-openwebui-named-tools-bootstrap-plan.md)

- [ ] M3.8 — Подключить canonical `Open WebUI Knowledge + external ingestion + Qdrant` contour
  Нужно сделать:
  - определить production-ready handoff от user-facing `Open WebUI` uploads/Knowledge UX к внешнему ingestion pipeline;
  - подключить `Qdrant` как canonical vector backend для Knowledge;
  - определить, какие части corpus metadata и collection lifecycle принадлежат `Open WebUI`, а какие backend/admin contour;
  - зафиксировать retrieval contract так, чтобы follow-up по документу опирался на `document_ref` и `Qdrant`, а не на один сохранённый deep-отчёт;
  - зафиксировать smoke path для native Knowledge chat на реальных документах без подмены explicit tools.
  Acceptance:
  - обычный вопрос по документу закрывается native Knowledge contour без hidden backend tool routing;
  - retrieval source-of-truth понятен и проверяем;
  - состояние документа и состояние конкретного запуска анализа разведены явно и не смешиваются;
  - parsing/OCR/tables не живут только внутри UI-процесса;
  - `Qdrant` используется как canonical vector backend, а не side experiment.
  Plan:
  - [Open WebUI-First RAG Architecture Alignment Implementation Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-10-openwebui-first-rag-architecture-plan.md)
  - [Qdrant Knowledge Base Store Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-01-qdrant-knowledge-base-store-plan.md)
  Progress 2026-04-13:
  - добавлена read-only operator summary `GET /operator/rag/qdrant/summary` с разделением backend collection и native `Open WebUI Knowledge` namespace;
  - `operator/state` теперь публикует `qdrantSummary` для UI/diagnostics;
  - добавлен CLI smoke `python tests/harness/openwebui/qdrant_namespace_smoke.py`, который проверяет separation, наличие native `Knowledge` collections и backend `session` points.
  Progress 2026-04-14:
  - ordinary chat upload в `Open WebUI` теперь проходит через backend-owned `session RAG`: patch-layer переводит upload path с raw client model на server-side wrapper `llm-tools-platform`, но клиентский `POST /api/chat/completions` остаётся на `raw.qwen-14b-llm`;
  - источник живого дефекта был в том, что `Open WebUI` использует alias `open_webui.utils.chat.generate_openai_chat_completion`; патч теперь подменяет и `routers.openai`, и этот alias, поэтому обычная chat-загрузка реально доходит до backend wrapper;
  - `tests/harness/openwebui/openwebui_followup_payload_harness.py` расширен режимами `session`, `knowledge`, `full`, `diagnostic`; diagnostic режим сохраняет `operatorSummary`, `open-webui` logs, `agent-api` docker logs, `tmux` tail и browser console/page errors;
  - live smoke `--mode session` и `--mode diagnostic` подтверждён: ordinary upload даёт grounded answer с цитатой, в `Qdrant` растёт только `rag_chunks_v1`, а native `Knowledge` остаётся в `anp-openwebui_*`; separation остаётся `true`.

- [ ] M3.9 — Принять финальное решение по роли `ask_document`
  Нужно сделать:
  - выбрать между `thin adapter over canonical Knowledge retrieval` и `compatibility-only tool`;
  - закрепить, что `ask_document` работает от `document_ref + question`, а `analyze_document_deep` — от `document_ref + analysis_goal`, и это разные состояния;
  - не оставлять параллельно два независимых document-QA контура без общего source-of-truth;
  - обновить tool catalog, docs и smoke matrix по факту принятого решения.
  Acceptance:
  - implementer и пользователь понимают, когда использовать native Knowledge chat, а когда explicit tool;
  - `ask_document` больше не висит в ambiguous middle state;
  - новый запрос с другим `question` или `analysis_goal` может дать новый результат по тому же документу, не будучи привязанным к одному старому deep-ответу;
  - document QA и explicit analysis/compare не дублируют друг друга.
  Plan:
  - [Open WebUI-First RAG Architecture Alignment Implementation Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-10-openwebui-first-rag-architecture-plan.md)

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
  - `M3.6 — Responsibility Split` и `M3.7 — Open WebUI-native Config` завершены.
  Plan:
  - [Unified Model Catalog / Gateway Implementation Plan](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-08-unified-model-catalog-gateway-plan.md)
- [ ] M4.2 — Определить coexistence contract для `Chainlit` и `Open WebUI`
  Нужно сделать:
  - зафиксировать роль `Chainlit` как debug/dev shell;
  - определить sunset criteria и минимальный residual support contract;
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

1. M3.7 — перенести runtime config инструментов в `Open WebUI`-native bootstrap/config contour
2. M3.8 — подключить canonical `Open WebUI Knowledge + external ingestion + Qdrant` contour
3. M2.1 / M2.2 — довести upload/document binding как backend-owned model
4. M2.5 / M2.6 — вынести parsing/corpus admin в backend-owned ingestion + admin UI contour
5. M3.9 — принять финальное решение по `ask_document`
6. M4.2 — оформить `Chainlit -> temporary compatibility/debug shell` и sunset contract
7. M3.1 — довести `Open WebUI` compose contour до pinned-tag + стабильного runtime smoke
8. Unified Model Catalog / Gateway — только после закрытия `M3.7`, `M3.8`, `M3.9` и стабилизации primary contour
9. M4.4 / M4.1 — довести tool registry и MCP/coexistence layer без возврата hidden routing
10. M3.3 — оставить как regression/smoke checkpoint для legacy `User Tool Server` path, а не как основной продуктовый milestone

## Risks / Blockers / Workarounds

- Риск: drift между `TASKS.md`, `README.md`, `AGENTS.md` и migration docs.
  Митигатор: все migration decisions сначала фиксировать здесь, затем обновлять docs в том же slice.
- Риск: случайно сломать текущий `Chainlit` path под видом cleanup старого `Open WebUI`.
  Митигатор: не переименовывать shared uploads path и не трогать backend compatibility hooks без explicit acceptance.
- Риск: native `Open WebUI Knowledge` и backend explicit tools начнут расходиться как два независимых retrieval мира.
  Митигатор: единый corpus/retrieval source-of-truth, `M3.8`, `M3.9`, чёткое разделение `Knowledge chat` vs `explicit tools`.
- Риск: `Open WebUI` станет вторым decision engine через hidden routing между Knowledge и tools.
  Митигатор: четыре явных режима (`plain model` / `native knowledge` / `explicit tools` / `agent mode`) и запрет hidden backend tool routing вне explicit contours.
- Risk: follow-up поведение external/community tools в `Open WebUI` легко интерпретировать по тексту ответа неверно.
  Митигатор: использовать `tests/harness/openwebui/openwebui_followup_payload_harness.py` и считать source-of-truth именно второй `POST /api/chat/completions`, а не chat text; contaminated runs с memory/extra tools считаются невалидными.
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

### 2026-04-10 — Architecture alignment after Open WebUI/Qdrant/tools addendum

- Решение: `Open WebUI-first` теперь фиксируется и в operational backlog, а не только в планах.
- Решение: ordinary document QA относится к native `Open WebUI Knowledge`, а explicit backend tools — к structured analysis/compare/equipment workflows.
- Решение: `Qdrant` фиксируется как canonical vector backend, а `Docling` first / `Tika` fallback — как canonical external ingestion path.
- Решение: роль `ask_document` переносится в отдельный decision slice `M3.9`; не считаем его автоматически primary UX-path.
- Решение: архитектурно критично разделять состояние документа и состояние конкретного запуска анализа; follow-up по документу должен идти через `document_ref` и retrieval source-of-truth, а не через один ранее сохранённый deep-отчёт.
- Progress: `TASKS_MIGRATION.md` синхронизирован с [2026-04-10-openwebui-first-rag-architecture-plan.md](/home/seral/HDD/proj/agent-navigator-pro/docs/plans/migration/2026-04-10-openwebui-first-rag-architecture-plan.md), master-plan и обновлёнными `Qdrant` / `tool-server` / responsibility docs.

### 2026-04-07 — Initial migration ledger seeding

- Решение: `TASKS_MIGRATION.md` становится primary operational backlog для migration scope.
- Решение: `TASKS.md` остаётся общепроектным backlog и хранит только краткую migration-ссылку и cross-project references.
- Решение: на тот момент default framing было `Chainlit-first today`, `Open WebUI controlled eval next`; позже это решение пересмотрено и заменено `Open WebUI-first` alignment note от `2026-04-10`.
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
- [x] Docs и policy reconciled под последнюю canonical migration framing
- [x] `scripts/run_openwebui.sh` больше не поднимает shadow runtime и остаётся compose-only compatibility helper
- [x] `Open WebUI` bootstrap/admin policy вынесена в `backend/.env` contract и показана в operator `Secrets / Access`
- [x] Operator surface временно закрыт backend-level `localhost-only` guard до отдельной auth/ACL phase
