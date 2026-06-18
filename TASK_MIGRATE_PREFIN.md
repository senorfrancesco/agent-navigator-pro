# TASK_MIGRATE_PREFIN - Open WebUI deep tools contour

> Предфинальный план выравнивания `Open WebUI`-контура после архитектурного разворота: `Open WebUI` остаётся владельцем обычного чата, модель подключается через внешний `OpenAI-compatible` endpoint, а наши `LangGraph`-графы работают как инструменты через отдельный `Tool/Workflow Server`.

## Целевое решение

`Open WebUI` остаётся владельцем чата, выбранной модели, истории, файлов, знаний, источников и пользовательского интерфейса.

LLM/VL inference подключается как внешний `OpenAI-compatible` provider: `llama-server`, `LiteLLM`, cloud endpoint или другой совместимый backend.

Модель, путь к весам и runtime-флаги задаются в контейнере конкретного inference runtime через `docker-compose`, `.env` и command flags. `Open WebUI` не должен управлять этими флагами; он подключается к уже поднятому provider URL.

`Tool/Workflow Server` исполняет наши `LangGraph`-инструменты, ведёт состояние долгих заданий и отдаёт `OpenAPI`-схему для `Open WebUI`.

`RAG-service` отвечает за ingestion, parsing, chunking, embeddings, reranking, работу с `Qdrant` и проверку embedding-профиля коллекций.

Новый backend-код для целевого контура размещается в `backend/app`. Старый `backend/orchestrator` остаётся совместимым слоем до поэтапного переноса: новые клиенты, server инструментов, workflows, `RAG-service` и `document-runtime` не должны расширять старый монолит без отдельной причины.

Главная цель: убрать псевдомодель `llm-tools-platform` и не делать отдельный чат-оркестратор. Пользователь выбирает обычную модель в `Open WebUI`; инструменты работают поверх неё, если они явно подключены и переданы модели.

## Почему нужен этот разворот

Старый предфинальный план привязывал обычный чат к backend raw proxy и считал `UMS` основным путём инференса. Это было полезным промежуточным шагом, но как целевой дизайн он оставляет слишком много лишней оркестрации:

- `Open WebUI` зависит от backend model manager даже для обычного чата;
- выбранная модель может снова смешаться с compatibility path;
- форк `Open WebUI` начинает включать runtime model selector, folder scan и load/status UX вместо узкой поддержки долгих tools;
- `Tool/Workflow Server` рискует стать второй псевдомоделью, а не исполнителем инструментов.

Целевое поведение должно быть ближе к нативному `Open WebUI`: обычный чат идёт напрямую в выбранный `OpenAI-compatible` provider, native `Knowledge/RAG` остаётся в штатном контуре `Open WebUI`, а наши инструменты вызываются только через tool calling.

## Нормативный поток

1. Пользователь выбирает модель в `Open WebUI`.
2. `Open WebUI` отправляет обычный `/v1/chat/completions` во внешний provider.
3. Если `tools` не переданы, ответ возвращается напрямую потоковой выдачей.
4. Если `tools` переданы, модель получает описание инструментов из `OpenAPI Tool Server`.
5. Когда модель вызывает инструмент, `Open WebUI` вызывает `Tool/Workflow Server`.
6. `Tool/Workflow Server` запускает конкретный `LangGraph`-граф.
7. Если инструмент быстрый, он возвращает результат сразу.
8. Если инструмент долгий, он возвращает `job_id/status_url`, пишет прогресс и поддерживает отмену.
9. Если инструменту нужен LLM/VL/RAG-вызов, он использует env-driven clients: `LLM_BASE_URL`, `LLM_MODEL_ID`, `VL_BASE_URL`, `RAG_SERVICE_URL`, `EMBEDDER_BASE_URL`, `RERANKER_BASE_URL`.
10. Финальный ответ формирует выбранная модель в обычном `Open WebUI` tool calling loop.

`LLM_MODEL_ID` в backend tools является fallback-моделью, а не способом запуска `llama-server`. Если `Open WebUI` передал текущую выбранную модель в tool context, tool использует её. Если текущая модель не передана, tool использует `LLM_MODEL_ID`. Если нет ни текущей модели, ни fallback, tool должен вернуть понятную configuration error.

Это обычная LLM с `tool calling`, а не отдельный агентный чат.

## Новые целевые режимы

- [ ] Обычный режим: выбранная модель без `tools` идёт напрямую в `OpenAI-compatible` provider.
  Комментарий: старый raw proxy через backend считается промежуточным legacy path. Целевой путь: `Open WebUI -> llama-server/LiteLLM/другой provider`.

- [ ] Режим инструментов: выбранная модель с `tools` вызывает `Tool/Workflow Server`.
  Комментарий: `Tool/Workflow Server` исполняет только конкретный tool call, не становится владельцем всего чата.

- [ ] Режим долгих инструментов: deep tool возвращает `job_id/status_url`, а форк `Open WebUI` показывает панель, polling, result materialization и cancel.
  Комментарий: это единственная обязательная причина держать минимальный форк `Open WebUI`.

- [ ] Debug/legacy режим: старые compatibility endpoints скрыты из пользовательского сценария.
  Комментарий: `llm-tools-platform` можно оставить только как временный диагностический путь с отдельной датой удаления.

## Задачи реализации

### M-PREFIN.0 - Ввести чистый backend-контур `backend/app`

- [x] Создать минимальный пакет `backend/app` для целевой реализации.
  Комментарий: начат первый срез: добавлены `backend/app/clients/openai_compatible.py` и `backend/app/tool_server/server.py`; старый `backend/orchestrator` не переписан.

- [x] Добавить проверочную конфигурацию клиента `OpenAI-compatible`.
  Комментарий: клиент читает `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ID` и предпочитает `current_model_id`, если он передан из контекста инструмента.

- [x] Добавить минимальный каркас `Tool/Workflow Server`.
  Комментарий: добавлены тестовый `/tools/echo`, рабочий `/tools/analyze_equipment_fast`, accepted route `/tools/analyze_equipment_deep` и экспорт `/tool-server/openapi.json` как проверочный контракт нового слоя.

- [x] Перенести первый рабочий fast-tool в `backend/app`.
  Комментарий: перенесён текстовый `analyze_equipment_fast`; он использует внешний `OpenAI-compatible` provider, текущую выбранную модель или fallback `LLM_MODEL_ID`, не вызывает `UMS` и не использует старый `execute_orchestration`.

- [x] Добавить минимальный job-контракт для deep tools.
  Комментарий: добавлены `jobs.py`, `openapi.py`, `GET /tool-jobs/{job_id}`, `GET /tool-jobs/{job_id}/result`, `POST /tool-jobs/{job_id}/cancel`; state переиспользует существующий `orchestrator.tool_job_store`.

- [x] Подключить новый `backend/app` к текущему запуску без поломки совместимых routes.
  Комментарий: новый clean-контур смонтирован в `orchestrator.agent_api` под `/app-tools`; существующие `/tool-server`, `/tools` и `/tool-jobs` остаются совместимым старым контуром.

Acceptance:

- Новый код добавляется в `backend/app`, а не раздувает `backend/orchestrator`.
- Старые маршруты остаются рабочими до отдельного migration slice.
- Первый рабочий tool может быть перенесён без изменения форка `Open WebUI`.

Verification:

- `pytest backend/tests/test_app_clean_contour.py -q`.
- `python -m py_compile backend/app/clients/openai_compatible.py backend/app/tool_server/server.py backend/app/tool_server/schemas.py backend/app/tool_server/registry.py backend/app/tool_server/handlers.py backend/app/tool_server/jobs.py backend/app/tool_server/openapi.py backend/orchestrator/agent_api.py`.
- `git diff --check`.

### M-PREFIN.1 - Очистить пользовательский список моделей

- [x] Убрать `llm-tools-platform` из `/v1/models` как обычную пользовательскую модель.
  Комментарий: выполнено в текущем backend compatibility path; при переходе на внешний provider этот пункт должен подтверждаться уже на стороне `Open WebUI` provider list.

- [ ] Не публиковать неполные runtime-записи как chat models.
  Комментарий: в целевом контуре список моделей приходит от внешнего provider. Неполные локальные записи не должны попадать в `Open WebUI` через bootstrap или ручной import.

- [ ] Зафиксировать debug-инвентарь моделей отдельно от пользовательского списка.
  Комментарий: diagnostics может жить в operator/admin surface, но не в `Open WebUI` chat selector.

Acceptance:

- `Open WebUI` показывает только реальные chat/VL models от настроенных providers.
- `llm-tools-platform` отсутствует в обычном списке моделей.
- Debug inventory не подменяет provider list.

Verification:

- `Open WebUI` provider list smoke.
- `curl` к provider `/v1/models`.
- `git diff --check`.

### M-PREFIN.2 - Перевести обычный чат на внешний `OpenAI-compatible` provider

- [ ] Зафиксировать provider contract в env/compose/native launcher.
  Комментарий: минимум: `OPENAI_API_BASE_URL`, `OPENAI_API_KEY`, default model. Для backend tools отдельно: `LLM_BASE_URL`, `LLM_MODEL_ID`.

- [ ] Зафиксировать inference runtime contract на уровне контейнера.
  Комментарий: путь к модели, порт, context size, GPU layers, device placement и дополнительные `llama-server` flags задаются в `docker-compose`/`.env`/command, а не через `Open WebUI` или `Tool/Workflow Server`.

- [ ] Развести provider URL и runtime flags.
  Комментарий: `Open WebUI` знает только `OPENAI_API_BASE_URL`/ключ/provider models; flags вроде `LLM_MODEL_FILE`, `LLM_CTX_SIZE`, `LLM_GPU_LAYERS`, `LLM_CUDA_DEVICES` принадлежат контейнеру inference runtime.

- [ ] Убрать backend raw proxy как целевой путь обычного чата.
  Комментарий: текущий raw proxy полезен как compatibility bridge, но не должен быть новым обязательным слоем.

- [ ] Проверить потоковую выдачу напрямую через provider.
  Комментарий: цель - вернуть максимально нативное поведение `Open WebUI`, включая streaming и штатные controls.

Acceptance:

- Обычный вопрос без tools не вызывает backend orchestration.
- Обычный вопрос без tools не требует `Tool/Workflow Server`.
- Потоковая выдача идёт из provider без искусственной сборки ответа в один chunk.
- Ответы не содержат `Timing / Quality`.
- Админ может сменить модель или runtime-флаги через env/compose restart без изменения `Open WebUI` fork code.

Verification:

- live smoke в `Open WebUI`.
- provider logs: `/v1/chat/completions`.
- backend logs: отсутствует вызов orchestration для plain chat.

### M-PREFIN.3 - Сделать корректный `Tool/Workflow Server`

- [x] Зафиксировать начальный server layout.
  Комментарий: созданы `schemas.py`, `registry.py`, `handlers.py`, `jobs.py`, `openapi.py` и `server.py`.

- [x] Экспортировать проверочный инструмент через `/tool-server/openapi.json`.
  Комментарий: добавлен минимальный контракт `echo` и рабочий контракт `analyze_equipment_fast`; следующий шаг - подключить постоянное состояние заданий для deep tools.

- [x] Разделить быстрые и долгие инструменты.
  Комментарий: быстрые возвращают результат сразу; `analyze_equipment_deep` возвращает `202`/`job_id/status_url` как accepted job.

- [x] Поддержать `GET /tool-jobs/{job_id}`, `GET /tool-jobs/{job_id}/result`, `POST /tool-jobs/{job_id}/cancel`.
  Комментарий: это базовый контракт для UI polling, materialization и отмены.

- [ ] Все LLM/VL/RAG-вызовы внутри tools перевести на env-driven clients.
  Комментарий: для `analyze_equipment_fast` LLM-вызов уже идёт через env-driven `OpenAI-compatible` client; остальные tools остаются следующими срезами.

Acceptance:

- Новый инструмент добавляется через registry + schema + handler.
- `Open WebUI` может проверить и импортировать `OpenAPI Tool Server`.
- Долгий tool можно отменить, и cancel доходит до backend job state.
- `Tool/Workflow Server` не отвечает на обычный чат.

Verification:

- targeted tests для `/tool-server/openapi.json`.
- targeted tests для fast/deep/cancel/result contract.
- live smoke через `Open WebUI` tool picker.

### M-PREFIN.4 - Минимизировать форк `Open WebUI` до deep tools only

- [x] Создать ветку форка без runtime model selector.
  Комментарий: в `/home/seral/HDD/proj/open-webui` создана ветка `anp/deep-tools-only-v0.9.2`; runtime model selector, folder scan и load/status слой сняты обратными коммитами.

- [x] Оставить поддержку deep-job panel.
  Комментарий: сохранены deep-job routes/API/store/display/polling/result materialization.

- [x] Оставить cancel path для deep jobs.
  Комментарий: `Open WebUI` вызывает `/api/v1/deep-jobs/{job_id}/cancel`, который проксирует cancel в `Tool/Workflow Server`.

- [x] Исправить parsing `<details>` для deep-job summary.
  Комментарий: `fix(deep-jobs): preserve details summary without body` оставлен в ветке deep-tools-only.

- [ ] Убрать из целевого форка все следы runtime model management.
  Комментарий: дополнительные проверки перед merge: нет `runtime_models`, `RuntimeModelFolderModal`, `ModelLoadStatus`, `enable_agent_navigator_runtime_models`.

Acceptance:

- Форк отличается от upstream только deep tools слоем и необходимыми тестами.
- Селектор моделей, scan folders, model load/status UX отсутствуют.
- Удаление/добавление model provider остаётся штатной функцией `Open WebUI`, а не нашей fork-specific логикой.

Verification:

- `rg -n "runtime_models|RuntimeModel|enable_agent_navigator_runtime_models" backend src -S` в форке.
- `pytest backend/open_webui/test/apps/webui/routers/test_deep_jobs.py backend/open_webui/test/utils/test_long_running_tools.py backend/open_webui/test/utils/test_tools_model_context.py -q`.
- `npm run test:frontend -- src/lib/utils/marked/extension.test.ts --run`.

### M-PREFIN.4.1 - Привязать `deep-jobs` к конкретному server/tool contract

- [ ] Убрать глобальный implicit fallback `deep-jobs` на `http://127.0.0.1:8000`.
  Комментарий: текущий форк `Open WebUI` при открытии любого чата опрашивает `/api/v1/chats/{chat_id}/deep-jobs/active`, а backend proxy без явной конфигурации пытается ходить в дефолтный `Tool/Workflow Server` на `127.0.0.1:8000`. Если этот сервер не поднят, обычный чат получает шумные `500`, хотя конкретный `Workspace Tool` или внешний tool server могут вообще не использовать этот контур.

- [ ] Сделать server binding явной частью `deep-job` состояния.
  Комментарий: `deep-job` должен знать, к какому `tool_server_id` / `base_url` / `tool_name` / `status_url` он относится. Polling active/status/result/cancel не должен угадывать единственный глобальный backend; он должен брать endpoint из сохранённого job snapshot или из connection metadata конкретного tool server.

- [ ] Развести `OpenAPI Tool Server`, `Workspace Tool` и legacy `llm-tools-platform` по lifecycle.
  Комментарий: если job создан через `OpenAPI Tool Server`, панель использует его `status_url` и `cancel_url`; если обычный `Workspace Tool` работает без backend-owned `tool_jobs`, панель `deep-jobs` не должна опрашивать внешний сервер; legacy `llm-tools-platform` не должен оставаться скрытым дефолтом для всех чатов.

- [ ] Добавить graceful degradation для отсутствующего tool server.
  Комментарий: `GET /api/v1/chats/{chat_id}/deep-jobs/active` при недоступном server должен возвращать `{"job": null}` или явный non-fatal unavailable state, а не `500`. Ошибка подключения к одному tool server не должна ломать открытие чата, список чатов или обычный model response.

- [ ] Зафиксировать конфигурационный контракт для нескольких tool servers.
  Комментарий: `Open WebUI` должен хранить/получать список tool server connections с `id`, `name`, `base_url`, `auth`, `openapi_url`, enabled-state и health; `deep-jobs` proxy должен работать через этот registry, а не через один process-wide `DEFAULT_TOOL_SERVER_BASE_URL`.

Acceptance:

- Открытие чата без активных долгих задач не обращается к несуществующему `127.0.0.1:8000`.
- Отсутствующий `Tool/Workflow Server` не даёт `500` на `/api/v1/chats/{chat_id}/deep-jobs/active`.
- Для активной долгой задачи сохранены `tool_server_id`, `tool_name`, `job_id`, `status_url`, `result_url` и `cancel_url`.
- `Cancel` и polling идут в тот server, который создал конкретный job.
- `Workspace Tool`, который сам синхронно вызывает внешний сервис, не активирует `deep-jobs` polling без явного backend-owned job contract.

Verification:

- targeted tests для `backend/open_webui/routers/deep_jobs.py` и `backend/open_webui/services/deep_jobs.py`: unavailable server -> `job: null`, active job -> correct server URL, cancel -> correct server URL.
- live smoke: открыть чат при выключенном `Tool/Workflow Server` и убедиться, что нет `500` в `/deep-jobs/active`.
- live smoke: запустить deep tool с явно подключенного server и проверить `status/result/cancel` по сохранённому `status_url`.
- `git diff --check`.

### M-PREFIN.5 - Убрать `llm-tools-platform` из пользовательского сценария

- [ ] Скрыть compatibility model из `Open WebUI` bootstrap/default config.
  Комментарий: named tools и `OpenAPI Tool Server` остаются, псевдомодель не нужна.

- [ ] Зафиксировать срок удаления legacy OpenAI-compatible wrapper path.
  Комментарий: если wrapper остаётся для диагностики, он должен быть явно debug-only.

- [ ] Убрать служебные блоки из user-facing tool results.
  Комментарий: `Timing / Quality` и telemetry остаются в logs/metadata, не в тексте ассистента.

Acceptance:

- Пользователь выбирает обычную модель.
- Инструменты включаются как tools, а не через специальную модель.
- Старый wrapper не появляется в onboarding/bootstrap.

Verification:

- live smoke нового чата.
- проверка bootstrap export/materialized state.
- targeted tests по tool result text sanitation.

### M-PREFIN.6 - Ввести общий `llm_client` для backend tools

- [x] Добавить backend-конфигурацию и HTTP-вызов клиента для chat/completions.
  Комментарий: `backend/app/clients/openai_compatible.py` теперь вызывает `/chat/completions` у внешнего `OpenAI-compatible` provider.

- [x] Сделать `LLM_MODEL_ID` fallback, а не единственным источником модели.
  Комментарий: порядок выбора модели внутри tool: `current_model_id` из `Open WebUI` tool context -> `LLM_MODEL_ID` из env -> configuration error.

- [ ] Добавить backend client для embeddings.
  Комментарий: клиент читает `EMBEDDER_BASE_URL`, `EMBEDDER_API_KEY`, `EMBEDDER_MODEL_ID`.

- [ ] Добавить backend client для VL/OCR-capable model.
  Комментарий: client optional; если `VL_BASE_URL`/`VL_MODEL_ID` не заданы, VL tools не регистрируются или деградируют понятно.

- [x] Перевести один fast-tool с legacy inference client на новый `llm_client`.
  Комментарий: текстовый `analyze_equipment_fast` в `backend/app` больше не зависит от model manager API; документный equipment graph остаётся compatibility path.

Acceptance:

- Workflow не зависит от model manager API.
- Замена модели делается env-параметрами и рестартом соответствующего сервиса.
- Инструмент может использовать текущую выбранную модель из `Open WebUI`, а `LLM_MODEL_ID` остаётся fallback для автономного запуска и тестов.
- Ошибка endpoint возвращает понятную degraded ошибку tool job, а не ломает обычный чат.

Verification:

- targeted tests для client config.
- targeted workflow test.
- live smoke одного fast/deep tool.

### M-PREFIN.7 - Выделить `RAG-service`

- [ ] Описать `RAG-service` как отдельный контейнер.
  Комментарий: он владеет ingestion, chunking, embeddings, reranking, `Qdrant` collection operations и embedding-profile checks.

- [ ] Зафиксировать env-контракт.
  Комментарий: минимум: `QDRANT_URL`, `EMBEDDER_BASE_URL`, `EMBEDDER_MODEL_ID`, `RERANKER_BASE_URL`, `RERANKER_MODEL_ID`, `DOCUMENT_RUNTIME_URL`.

- [ ] Развести Open WebUI native Knowledge и backend specialized tools.
  Комментарий: обычный вопрос по базе знаний идёт через native Knowledge; deep analysis остаётся tool/job.

- [ ] Поддержать external/attached `Qdrant` collections с явным embedding profile.
  Комментарий: внешняя коллекция без профиля не должна использоваться для поиска молча.

Acceptance:

- `Qdrant` остаётся retrieval storage, а не состоянием tools.
- `RAG-service` может индексировать и искать без участия обычного chat provider.
- Смена embedding-модели создаёт новую projection/collection, а не смешивает векторы.

Verification:

- targeted tests projection metadata.
- live smoke ingest/retrieve.
- проверка `Qdrant` collection metadata.

### M-PREFIN.8 - Выделить `document-runtime`

- [ ] Описать `document-runtime` endpoints: `POST /parse`, `POST /ocr`, `GET /health`, `GET /metrics`.
  Комментарий: `Docling`, `Tika`, OCR/VL extraction и тяжёлые parsing dependencies живут отдельно от chat и tools.

- [ ] Зафиксировать pipeline загрузки документа.
  Комментарий: file upload -> `document-runtime` -> chunker -> embedding -> `Qdrant`.

- [ ] Развести обычный ingestion и deep document analysis.
  Комментарий: extraction для поиска не равен deep-report; deep-report остаётся отдельным job.

Acceptance:

- Сбой parsing/OCR не убивает LLM runtime.
- Для больших файлов есть timeout/retry/status.
- Результат parsing имеет стабильный contract для chunking.

Verification:

- targeted tests parse/OCR contract.
- live smoke PDF/image/invalid file.
- RSS/timeout check на большом файле.

### M-PREFIN.9 - Выделить `reranker-runtime`

- [ ] Проверить фактическую семантику `RAG_RERANKING_ENGINE` в текущем `Open WebUI`.
  Комментарий: пустое значение нельзя считать disabled без проверки кода.

- [ ] Сделать отдельный runtime с `POST /v1/rerank`, `GET /health`, `GET /models`, `GET /metrics`.
  Комментарий: reranker имеет другой профиль нагрузки, чем embeddings и LLM.

- [ ] Подключить `RAG-service` к reranker endpoint.
  Комментарий: `Open WebUI` может использовать native external reranker, если версия это поддерживает; иначе rerank живёт внутри `RAG-service`.

Acceptance:

- Reranking можно включить/выключить явно.
- Сбой reranker деградирует качество retrieval, но не валит обычный чат.
- Метрики reranker отделены от LLM и embeddings.

Verification:

- targeted tests rerank adapter.
- live smoke с включённым/выключенным rerank.
- latency check на top-k rerank.

### M-PREFIN.10 - Проверить failure domains

- [ ] Прогнать чат во время массовой индексации.
  Комментарий: смотреть TTFT, tokens/sec, p95/p99 LLM latency.

- [ ] Прогнать несколько параллельных uploads.
  Комментарий: смотреть RSS `RAG-service`, RSS `document-runtime`, embedding latency, `Qdrant` insert latency.

- [ ] Проверить перезапуск `embedding-runtime`.
  Комментарий: должен деградировать RAG/ingestion, но не обычный LLM chat.

- [ ] Проверить перезапуск `document-runtime`.
  Комментарий: должен деградировать parsing/OCR и ingestion, но не обычный chat.

- [ ] Проверить перезапуск `Tool/Workflow Server`.
  Комментарий: должен остановить/перевести в failed только активные tools, но не ломать plain chat.

Acceptance:

- У каждого runtime есть понятное degraded-состояние.
- Обычный чат не проседает критически из-за Knowledge indexing.
- Метрики позволяют отличить LLM saturation, embedding saturation, reranker saturation и document parsing saturation.

Verification:

- нагрузочный smoke script или e2e suite.
- `nvidia-smi`, process RSS, runtime `/metrics`, `Qdrant` latency.
- отчёт с p95/p99 и failure-domain выводами.

## Архив / отклонённые направления

### A-PREFIN.1 - Runtime model management в форке `Open WebUI`

Статус: отклонено для целевого `deep-tools-only` форка.

Решение:

- не переносить folder scan, model registration, model load/status и runtime params в минимальный форк;
- не делать форк `Open WebUI` вторым model manager;
- использовать штатные provider settings `Open WebUI` для подключения `OpenAI-compatible` endpoints;
- если управление локальными моделями понадобится позже, проектировать его отдельным admin/control-plane продуктом, а не смешивать с deep tools UI.

Причина:

- этот слой снова делает `Open WebUI` зависимым от backend-specific model lifecycle;
- он расширяет форк за пределы единственной нужной функции: long-running tools UX;
- он мешает перейти к простой схеме `Open WebUI -> provider`, `Open WebUI -> Tool/Workflow Server`.

### A-PREFIN.2 - Обязательный backend raw proxy для обычного чата

Статус: legacy bridge, не целевой путь.

Решение:

- обычный чат должен идти в `llama-server`, `LiteLLM` или другой provider напрямую из `Open WebUI`;
- backend raw proxy может оставаться временно для диагностики, но не должен быть обязательной частью production contour.

### A-PREFIN.3 - Model manager как обязательный data-path

Статус: отклонено для целевой архитектуры.

Решение:

- model manager может существовать как необязательный operator/control-plane слой;
- hot path чата, embeddings, reranking, OCR и tools не должен требовать обязательного прохождения через него;
- замена модели в простом контуре делается env/compose/runtime restart, а не скрытой автоматикой внутри чата.

## Регрессии, которые нужно покрыть

- [ ] Plain chat не вызывает backend orchestration.
  Комментарий: проверять на новом provider path, а не только на старом raw proxy.

- [ ] Deep tool возвращает `job_id/status_url` и отображается как панель в `Open WebUI`.
  Комментарий: панель должна переживать reload и materialize terminal result.

- [ ] Cancel deep tool доходит до `Tool/Workflow Server`.
  Комментарий: UI cancel -> `/api/v1/deep-jobs/{job_id}/cancel` -> `/tool-jobs/{job_id}/cancel`.

- [ ] Tool result не содержит `Timing / Quality`.
  Комментарий: telemetry остаётся structured/log-only.

- [ ] Tools используют env-driven LLM/RAG clients.
  Комментарий: tool не должен требовать псевдомодель.

- [ ] `RAG-service` блокирует поиск по коллекции с несовпадающим embedding profile.
  Комментарий: особенно важно для external/attached `Qdrant` collections.

- [ ] Перезапуск одного runtime не валит соседние runtime.
  Комментарий: основной критерий корректного разделения контейнеров.

## Условия завершения

- [ ] Обычный чат в `Open WebUI` работает через внешний `OpenAI-compatible` provider.
- [ ] Форк `Open WebUI` содержит только deep tools слой и не содержит runtime model selector.
- [ ] `Tool/Workflow Server` публикует инструменты через `OpenAPI` и поддерживает status/result/cancel.
- [ ] `llm-tools-platform` отсутствует из пользовательского сценария.
- [ ] Backend workflows используют env-driven `OpenAI-compatible` clients.
- [ ] `RAG-service` и `document-runtime` описаны как отдельные контейнеры с env-контрактом.
- [ ] Native `Open WebUI Knowledge` и specialized tools разведены по ролям.
- [ ] Failure-domain smoke подтверждает, что обычный чат не зависит от ingestion/parsing/tools runtime.
