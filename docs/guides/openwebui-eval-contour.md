# Open WebUI Runtime Contour

Этот документ описывает текущий supported путь для `Open WebUI` в проекте как для основного пользовательского shell поверх уже работающего backend.

## Что это такое

- `Open WebUI` здесь рассматривается как основной пользовательский shell.
- Основной backend остаётся внешним `FastAPI`-контуром проекта.
- Текущий compatibility `/v1/chat/completions` — это legacy/product wrapper surface, а не финальный model-provider contract для `Open WebUI`.
- Для native tool-calling `Open WebUI` теперь должен использовать отдельный raw provider surface: `/raw/v1/models` и `/raw/v1/chat/completions`.
- Целевой следующий шаг — `OpenAPI Tool Server`, а не перенос предметной логики в `Open WebUI Functions`.

## Как запускать

1. Поднять backend canonical path любым поддерживаемым способом:
   - `./scripts/launcher.sh --target native --profile adaptive`
   - `./scripts/run_native.sh`
   - либо уже работающий host backend (`agent_api`, `document_server`, `legal_server`, `UMS`)
2. Поднять `Open WebUI` через текущий compose profile:

```bash
docker compose --profile legacy up -d open-webui
```

3. Открыть:

- `http://localhost:3001`

Важно:

- `run_native` поднимает только backend/UMS/Chainlit на host и публикует их на `0.0.0.0`;
- самого supported native-launch path для `Open WebUI` в репозитории сейчас нет;
- поэтому практический runtime path сегодня это `native backend + dockerized Open WebUI`, а не “всё полностью без Docker”.
- имя profile `legacy` здесь историческое и не описывает продуктовую роль `Open WebUI`.

## Что сейчас считается supported

- отдельный `Open WebUI` docker profile;
- connection к backend через два независимых surface:
  - raw model provider: `/raw/v1/*`
  - backend-owned tool server: `/tool-server/*`
- backend-owned tool UX control plane:
  - `/operator/tool-bindings`
  - `/operator/tool-actions/catalog`
  - `/operator/tool-bindings/export/openwebui`
- shared uploads volume через `backend/open_webui_uploads`;
- user-facing shell, history/admin и будущий tool-calling UX.

## Что сейчас намеренно не делаем

- не переносим orchestration policy, document lifecycle или retrieval truth внутрь `Open WebUI`;
- не включаем native `Open WebUI` RAG как source-of-truth для продукта;
- не делаем `MCP-first` integration path;
- не переименовываем `backend/open_webui_uploads` на этой фазе.

## Current Provider + Tool Setup

Текущий provider + tool contour для `Open WebUI`:

- model provider подключается отдельно как raw `OpenAI-compatible` connection:
  - `GET /raw/v1/models`
  - `POST /raw/v1/chat/completions`
- tools подключаются отдельно как `OpenAPI Tool Server`:
  - `GET /tool-server/openapi.json`
  - `POST /tool-server/tools/*`
  - `GET|POST /tool-server/tool-jobs/*`
- `/v1/chat/completions` остаётся только compatibility path для `agent-navigator` wrapper и не должен быть default provider для `Open WebUI` tool flows.

## User vs Global Tool Servers

Для следующей фазы важно учитывать модель `Open WebUI`:

- `User Tool Servers` подходят для user-scoped подключений и personal eval flows;
- `Global Tool Servers` подходят для admin-managed shared integrations.

На текущем этапе это ещё не реализовано в проекте, но guide и будущий MVP-plan исходят из этой модели.

## Current User Tool Server Setup

Проверенный UI path для первого `M3.3` smoke находится в:

- `Settings -> Integrations -> Add Connection`

Для текущего `OpenAPI Tool Server` важно не путать base URL и spec URL:

- в поле `URL` нужно указывать **base path** сервера, например `http://127.0.0.1:8000/tool-server`;
- `Open WebUI` сам добавляет `/openapi.json` при проверке соединения;
- если вставить полный spec URL `http://127.0.0.1:8000/tool-server/openapi.json`, UI попытается открыть `/tool-server/openapi.json/openapi.json`, и connection check упадёт.

Минимальная конфигурация:

- `Type`: `OpenAPI`
- `Name`: `Agent Navigator Tools`
- `URL`: `http://127.0.0.1:8000/tool-server`
- `Auth`: `Bearer`
- `API Key`: значение `OPENAPI_TOOL_SERVER_TOKEN` из `backend/.env`

Важно:

- этот `User Tool Server` path теперь считается debug-only contour для ручной диагностики transport layer;
- default bootstrap-managed contour больше не materialize’ит `Agent Navigator OpenAPI Tool Server` как chat-visible entry в picker;
- в обычном supported contour пользователь видит только named tools (`equipment_*`), а вызовы в backend tool server идут из thin wrappers/action functions.

## Current Raw Model Provider Setup

Для `model/tool split` provider нужно настраивать отдельно от tools:

- `Connections -> OpenAI-compatible`
- base URL: `http://127.0.0.1:8000/raw/v1`
- models endpoint materialize’ится через `/raw/v1/models`
- chat endpoint идёт через `/raw/v1/chat/completions`

Важно:

- этот raw provider не должен быть `agent-navigator` wrapper;
- он не делает document routing, RAG policy или tool dispatch;
- его задача — только protocol-clean chat/model surface поверх `UMS`.
- проверенный working contour теперь такой:
  - чат без tools идёт в `POST /raw/v1/chat/completions`;
  - fast tool после chat-level enable идёт в `POST /tool-server/tools/analyze_equipment_fast`;
  - deep tool после chat-level enable идёт в `POST /tool-server/tools/analyze_equipment_deep -> 202 Accepted`.

Важно:

- `Open WebUI` в этом contour работает как Docker-контейнер, поэтому у integration path есть dual-URL нюанс:
  - browser-side verify на хосте обычно видит native backend через `http://127.0.0.1:8000/tool-server`;
  - server-side refresh / materialize внутри контейнера видит тот же backend через `http://host.docker.internal:8000/tool-server`;
- из-за этого один и тот же `User Tool Server` сейчас может вести себя по-разному на этапе browser-side check и на этапе server-side refresh/import;
- `GET /tool-server/api/config` добавлен как terminal/Open WebUI-compatible config probe для prefixed surface;
- до отдельного proxy/policy slice текущий supported smoke path остаётся `native backend + dockerized Open WebUI` с явным учётом host/container split;
- для host/native backend используйте browser-reachable адрес (`127.0.0.1` / `localhost`) при локальных backend probes и отдельно проверяйте container-reachable адрес для dockerized `Open WebUI`;
- для container smoke через `run_all.sh` / `launcher.sh --target container` backend должен быть не только описан в `docker-compose.yaml`, но и уже иметь собранные локальные образы, потому что container runtime path теперь intentionally использует `docker compose up --no-build`.

## Current Tool UX Control Plane

Для следующего слоя `Prompts / Action Functions` backend теперь отдаёт отдельный control-plane catalog:

- `GET /operator/tool-bindings`
  - canonical binding catalog для direct actions, slash shortcuts и disabled document-dependent entries
- `GET /operator/tool-actions/catalog`
  - быстрый split между enabled direct actions, prompt shortcuts и blocked bindings
- `GET /operator/tool-bindings/export/openwebui`
  - import-ready export bundle для ручного bootstrap в `Open WebUI`

Что уже готово:

- direct-action foundation для `analyze_equipment_fast`
- direct-action foundation для `analyze_equipment_deep`
- prompt shortcuts `/hw_fast` и `/hw_deep` как backend-owned metadata
- `available_actions` в tool responses теперь содержат стабильные `binding_id` payloads для equipment flows
- export bundle теперь включает manual-bootstrap артефакты:
  - `browserReachableBaseUrl` и `containerReachableBaseUrl` для tool server
  - import-ready `Workspace Prompts`
  - 4 import-ready `Action Functions` templates:
    - `equipment_fast_action`
    - `equipment_deep_action`
    - `tool_job_refresh_action`
    - `tool_job_cancel_action`
  - `importChecklist` с ручными шагами для admin setup named tools в `Open WebUI`

Чего ещё нет:

- persistence/editing bindings через operator UI
- automatic import/deployment `Action Functions` в контейнер `Open WebUI`
- подтверждённый manual smoke самого import flow в `Open WebUI` admin UI
- document/compare direct actions до завершения backend-owned document binding

## Known Limits

- backend нужно поднимать отдельно через canonical runtime path; отдельного `run_openwebui.sh` больше нет;
- текущий `Open WebUI` runtime path остаётся зависимым от host/container split между browser-side URL и container-side URL;
- file handoff и document binding пока не переведены на backend-owned upload contract;
- legacy `/v1/chat/completions` не подходит как primary provider для native tool-calling, потому что это product wrapper path;
- raw `/raw/v1/chat/completions` уже отделён от wrapper-layer, а базовый smoke на topology `raw provider + enabled tool server` подтверждён;
- `MCP` пока не является основным путём интеграции;
- текущий storage path `backend/open_webui_uploads` носит legacy-имя, но считается живым shared contract.
- `Open WebUI` пока не подтверждён как автоматический poller для async tool jobs: deep tool показывает accepted/source payload с `job_id` и `status_url`, но final completed result backend пока не подтягивается в чат автоматически.
