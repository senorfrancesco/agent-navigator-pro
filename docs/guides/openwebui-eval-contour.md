# Open WebUI Eval Contour

Этот документ описывает текущий supported путь для `Open WebUI` в проекте: отдельный `legacy/eval` contour поверх уже работающего backend. Он не меняет product truth: canonical UI проекта сейчас — `Chainlit`.

## Что это такое

- `Open WebUI` здесь используется как candidate shell для controlled migration/evaluation.
- Основной backend остаётся внешним `FastAPI`-контуром проекта.
- Текущий OpenAI-compatible `/v1/chat/completions` — это временная compatibility/eval surface, а не финальный integration contract.
- Целевой следующий шаг — `OpenAPI Tool Server`, а не перенос предметной логики в `Open WebUI Functions`.

## Как запускать

1. Поднять backend canonical path любым поддерживаемым способом:
   - `./scripts/launcher.sh --target native --profile adaptive`
   - `./scripts/run_native.sh`
   - либо уже работающий host backend (`agent_api`, `document_server`, `legal_server`, `UMS`)
2. Поднять `Open WebUI` как отдельный profile:

```bash
docker compose --profile legacy up -d open-webui
```

3. Открыть:

- `http://localhost:3001`

## Что сейчас считается supported

- отдельный `Open WebUI` docker profile;
- connection к backend через существующий OpenAI-compatible surface;
- shared uploads volume через `backend/open_webui_uploads`;
- manual eval пользовательского shell, history/admin и будущего tool-calling UX.

## Что сейчас намеренно не делаем

- не считаем `Open WebUI` уже переключённым основным UI;
- не переносим orchestration policy, document lifecycle или retrieval truth внутрь `Open WebUI`;
- не включаем native `Open WebUI` RAG как source-of-truth для продукта;
- не делаем `MCP-first` integration path;
- не переименовываем `backend/open_webui_uploads` на этой фазе.

## Целевой следующий шаг

Следующий integration milestone для `Open WebUI`:

- backend подключается как `OpenAPI Tool Server`;
- tools становятся явными backend-owned контрактами;
- `/v1/chat/completions` остаётся совместимостью, но перестаёт быть целевым продуктовым интерфейсом интеграции.

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

- `User Tool Server` выполняется из браузерного клиента, поэтому backend URL должен быть доступен именно из браузера пользователя;
- для host/native backend используйте browser-reachable адрес (`127.0.0.1` / `localhost` в локальном dev path);
- для container smoke через `run_all.sh` / `launcher.sh --target container` backend должен быть не только описан в `docker-compose.yaml`, но и уже иметь собранные локальные образы, потому что container runtime path теперь intentionally использует `docker compose up --no-build`.

## Known Limits

- backend нужно поднимать отдельно через canonical runtime path; отдельного `run_openwebui.sh` больше нет;
- file handoff и document binding пока не переведены на backend-owned upload contract;
- OpenAI-compatible path не даёт финальной model/tool/job semantics для migration target;
- `MCP` пока не является основным путём интеграции;
- текущий storage path `backend/open_webui_uploads` носит legacy-имя, но считается живым shared contract.
