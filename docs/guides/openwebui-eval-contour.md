# Open WebUI Runtime Contour

Этот документ описывает текущий поддерживаемый контур `Open WebUI` после вывода `legacy_patch` и старого `Action Function` пути из рабочего сценария.

## Что сейчас считается рабочим путём

- `Open WebUI` остаётся основным пользовательским интерфейсом.
- Контейнерный сервис `open-webui` собирается из локального форка `/home/seral/HDD/proj/open-webui`, а не запускается из upstream-образа с runtime-патчем.
- Основной backend остаётся внешним `FastAPI`-контуром проекта.
- `Open WebUI` использует:
  - raw model provider: `/raw/v1/models`, `/raw/v1/chat/completions`
  - backend-owned tool server: `/tool-server/openapi.json`, `/tool-server/tools/*`, `/tool-server/tool-jobs/*`
  - собственный proxy-слой long-running задач: `/api/v1/deep-jobs/*`
- Поддерживаемый пользовательский сценарий только один:
  - запуск инструмента через нативный tool surface `Open WebUI`
  - панель long-running выполнения в чате
  - автообновление состояния
  - остановка через нативный `Stop`
  - terminal result и `Download report`

Не поддерживается:

- `/api/chat/actions/*` как рабочий путь long-running инструментов
- `deploy/openwebui_legacy_patch`
- импортируемые `Action Function` для `refresh/cancel`

## Как запускать

1. Поднять backend canonical path любым поддерживаемым способом:
   - `./scripts/launcher.sh --target native --profile adaptive`
   - `./scripts/run_native.sh`
   - либо уже работающий host backend (`agent-api`, `document-server`, `legal-server`, `UMS`)
2. Поднять контейнерный `Open WebUI`:

```bash
docker compose --profile legacy up -d open-webui
```

3. Открыть:

- `http://localhost:3001`

Важно:

- имя compose-профиля `legacy` сохранено только как историческое имя профиля;
- сам сервис `open-webui` уже не использует `legacy_patch` и собирается из локального форка;
- практический runtime path сегодня это `native backend + containerized Open WebUI from fork`.

## Provider и tool split

Текущий разделённый контур такой:

- raw model provider подключается отдельно как `OpenAI-compatible` connection:
  - `GET /raw/v1/models`
  - `POST /raw/v1/chat/completions`
- инструменты подключаются отдельно через backend-owned `OpenAPI Tool Server`:
  - `GET /tool-server/openapi.json`
  - `POST /tool-server/tools/*`
  - `GET|POST /tool-server/tool-jobs/*`
- `/v1/chat/completions` остаётся compatibility surface и не считается каноническим provider path для native tool-calling в `Open WebUI`.

Практический working contour:

- чат без инструментов идёт в `POST /raw/v1/chat/completions`;
- быстрый инструмент после включения в чате идёт в `POST /tool-server/tools/analyze_equipment_fast`;
- long-running инструмент идёт в `POST /tool-server/tools/analyze_equipment_deep`, получает `202 Accepted`, а дальнейшее состояние материализуется через panel-path и proxy `deep_jobs`.

## Bootstrap и поддерживаемая поверхность

`scripts/bootstrap_openwebui.py` остаётся общим механизмом синхронизации `Open WebUI`.

Поддерживаемая политика bootstrap:

- синхронизировать соединения и импортируемые ресурсы `Open WebUI`;
- публиковать пустой список `actionFunctions` в export bundle;
- удалять ранее установленные legacy `Action Function`, если они остались в существующей инсталляции;
- не возвращать `equipment_deep_action`, `tool_job_refresh_action` и `tool_job_cancel_action` в рабочий путь.

Итог:

- пользовательский long-running UX ограничен панелью, автообновлением и нативным `Stop`;
- cleanup legacy functions остаётся только миграционной страховкой для старых установок.

## Mixed-runtime и host/container split

`Open WebUI` остаётся контейнером, поэтому integration path по-прежнему должен учитывать split между хостом и контейнером:

- browser-side проверки обычно видят backend на `http://127.0.0.1:8000/...`;
- внутри контейнера тот же backend достигается через `http://host.docker.internal:8000/...`.

Из этого следуют правила:

- при ручной диагностике отдельно проверять browser-reachable и container-reachable адреса;
- для mixed-runtime smoke сначала подтверждать `/health`, `/tool-server/openapi.json` и container-side доступ до `agent-api` и `UMS`;
- учитывать, что `docker compose --profile legacy up` теперь собирает сервис из локального форка, а не берёт готовый upstream-образ.

## Что намеренно не делаем

- не переносим orchestration policy, document lifecycle и source-of-truth по состоянию задач внутрь `Open WebUI`;
- не используем native `Open WebUI` RAG как продуктовый источник истины;
- не делаем `MCP-first` integration path для этого слоя;
- не возвращаем `Action Function` как запасной пользовательский маршрут long-running задач.

## Automated Deep-Job Eval Slice

Для native-only long-running контура в репозитории есть отдельный локальный runner:

```bash
python tests/harness/openwebui/openwebui_deep_job_eval.py
```

Runner делает детерминированный pipeline:

- выполняет runtime preflight для `Open WebUI`, `agent-api`, `tool-server`, `document-server`, `legal-server`, `UMS` и container-side probes;
- запускает `bootstrap_openwebui.py` и пишет `bootstrap.json`;
- запускает таргетные backend-regression тесты:
  - `backend/tests/test_tool_bindings_actions.py`
  - `backend/tests/test_openapi_tools_api.py`
- запускает репозиторный `Playwright`-контур [deep-job.spec.ts](/home/seral/HDD/proj/agent-navigator-pro/tests/e2e/openwebui/deep-job.spec.ts);
- дочитывает фактический `request_payload` из `tool_jobs`, чтобы зафиксировать реальный `equipment_query`, а не только UI-эхо;
- складывает артефакты в `output/openwebui-deep-job-eval/<timestamp>/`.

Основные артефакты runner:

- `manifest.json`
- `runtime-preflight.json`
- `bootstrap.json`
- `pytest.log`
- `playwright.log`
- `results.raw.json`
- `results.enriched.json`
- `summary.md`

Важно:

- runner проверяет только native panel-path;
- он больше не использует `/api/chat/actions/*` и не проверяет `refresh/cancel action`;
- источником истины остаются `tool_jobs`, нативная панель `Open WebUI`, `Stop`, terminal result и `Download report`.

## Known Limits

- backend всё ещё нужно поднимать отдельно через canonical runtime path;
- mixed-runtime требует явного учёта host/container split;
- file handoff и document binding остаются backend-owned и не становятся native `Open WebUI` source-of-truth;
- storage path `backend/open_webui_uploads` носит legacy-имя, но остаётся живым shared contract;
- финальная приёмка контейнерного smoke без legacy-контуров ещё должна быть подтверждена живым прогоном после стабилизации среды.
