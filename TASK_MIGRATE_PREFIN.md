# TASK_MIGRATE_PREFIN - native Open WebUI tools contour

> Предфинальный план выравнивания `Open WebUI`-контура: чистая выбранная модель, нативный чат, нативный `RAG`, потоковый инференс через `UMS` и наши инструменты как исполнительный слой, а не как отдельная псевдомодель.

## Целевое решение

`Open WebUI` остаётся владельцем чата, выбранной модели, истории, файлов, знаний, источников и пользовательского интерфейса.

`UMS` отвечает за запуск и инференс выбранной модели.

`LangGraph` исполняет наши инструменты, но не подменяет обычный чат целиком.

Главная цель: не заставлять пользователя выбирать специальную модель `llm-tools-platform`. Пользователь выбирает любую доступную модель, а инструменты работают поверх неё.

## Почему нужен этот разворот

Текущий слой совместимости смешал две роли:

- обычная модель в `Open WebUI`;
- скрытый агентный контур `execute_orchestration`.

Из-за этого выбранная модель вроде `qwen-14b-llm` проваливается в `execute_orchestration`, получает служебные блоки, теряет нормальный потоковый вывод и загрязняет историю чата текстом `Timing / Quality`.

Целевое поведение должно быть ближе к нативному `Open WebUI`: модель отвечает как обычная LLM, `RAG` и источники остаются в штатном контуре `Open WebUI`, а наши инструменты вызываются только когда они действительно переданы модели как tools.

## Нормативный поток

1. Пользователь выбирает модель в `Open WebUI`, например `qwen-14b-llm`.
2. `Open WebUI` отправляет обычный `/v1/chat/completions`.
3. Если `tools` не переданы, запрос идёт почти напрямую в `UMS` и возвращает потоковый ответ.
4. Если `tools` переданы, модель получает описание инструментов.
5. Когда модель вызывает инструмент, исполнение уходит в наш `LangGraph`.
6. Если инструменту нужен LLM-вызов, он использует текущую выбранную модель.
7. Финальный ответ снова генерирует выбранная модель и потоково возвращает его в `Open WebUI`.

Это обычная LLM с `tool calling`, а не отдельный агентный чат.

## Режимы на время миграции

- [x] Обычный режим: выбранная модель без `tools` идёт прямым потоком в `UMS`.
  Комментарий: выполнено; raw-запросы без `tools` идут через прямой `UMS` proxy, сохраняют потоковую выдачу и не вызывают `execute_orchestration`.

- [x] Режим инструментов: выбранная модель с `tools` запускает лёгкий цикл `model -> tool call -> LangGraph tool executor -> model`.
  Комментарий: выполнено для native tool calling; live `Playwright` smoke `2026-04-27` подтвердил `analyze_equipment_deep` поверх выбранной `qwen-14b-llm` без выбора `llm-tools-platform`.

- [ ] Временный debug-режим для старого совместимого пути.
  Комментарий: `llm-tools-platform` не должен быть продуктовой моделью. Если он временно нужен для отладки, его нужно скрыть из обычного списка моделей и зафиксировать срок удаления.

- [ ] Полное удаление `llm-tools-platform` из пользовательского сценария.
  Комментарий: целевое состояние - отсутствие псевдомодели в `Open WebUI`. Инструменты должны работать с любой выбранной моделью.

## Задачи реализации

### M-PREFIN.1 - Очистить список моделей

- [x] Убрать `llm-tools-platform` из `/v1/models` как обычную пользовательскую модель.
  Комментарий: выполнено в `agent_api.py`; `/v1/models` теперь возвращает только chat-visible raw catalog.

- [x] Не отдавать модели со статусом `incomplete`, `unsupported` или `ambiguous`.
  Комментарий: выполнено через фильтрацию `UMS /models`; локальный fallback оставлен консервативным и требует runtime type, path и port.

- [x] Сохранить диагностический способ увидеть скрытые модели.
  Комментарий: полный каталог со status/status_reason остаётся в `UMS /models`; пользовательский `/v1/models` его не смешивает со списком выбора.

Acceptance:

- `/v1/models` отдаёт только готовые пользовательские `llm` / `vision` модели.
- Неполные записи видны в админском каталоге, но не выбираются для чата.
- `llm-tools-platform` отсутствует в обычном списке моделей.

Verification:

- `pytest backend/tests/test_agent_api_openai_compat.py -q`
- `curl -sS http://127.0.0.1:8000/v1/models`

### M-PREFIN.2 - Вернуть нативный поток для обычной модели

- [x] Изменить `/v1/chat/completions`: если `tools` нет, проксировать запрос в raw-контур `UMS`.
  Комментарий: выполнено через общий raw proxy helper; обычная выбранная модель использует `_request_raw_openai_infer`, `_open_raw_openai_stream` и `_proxy_raw_openai_stream`.

- [x] Не создавать `OrchestrationRequest` для обычного raw-чата.
  Комментарий: выполнено; plain `/v1/chat/completions` возвращается из raw-ветки до attachment discovery, session docs и `execute_orchestration`.

- [x] Вернуть потоковую генерацию без искусственного объединения ответа в один chunk.
  Комментарий: выполнено; stream-ответы raw-моделей проксируются из backend stream без `_stream_openai_compat_response`.

Acceptance:

- Для `qwen-14b-llm` без `tools` не вызывается `execute_orchestration`.
- Потоковый ответ содержит несколько SSE-событий от backend модели.
- Текст ответа не содержит `Timing / Quality`.

Verification:

- `pytest backend/tests/test_agent_api_openai_compat.py -q`
- ручной smoke в `Open WebUI` на выбранной raw-модели.

### M-PREFIN.3 - Вынести инструменты в лёгкий исполнительный цикл

- [x] Обработать запросы с `tools` отдельно от plain proxy.
  Комментарий: выполнено для native tool calling; `/v1/chat/completions` проксирует `tools` в raw-модель, а не в `execute_orchestration`.

- [x] Передавать в `LangGraph` только данные конкретного tool call.
  Комментарий: выполнено на backend-стороне через `OpenAPI Tool Server`; tool endpoint строит payload из конкретного `ToolRequest` и принимает текущий `model_id`.

- [x] Возвращать результат инструмента обратно в модель как tool result.
  Комментарий: подтверждено на границе `Open WebUI`: `function_call_output` преобразуется в `role=tool`, после чего финальный ответ снова запрашивается у выбранной модели; backend остаётся исполнителем конкретного tool call.

- [x] Все внутренние LLM-вызовы инструментов резолвить через текущую выбранную модель.
  Комментарий: backend tool executor применяет `resolved_model_id`, если он передан через `user_inputs.current_model_id` или `X-OpenWebUI-Model-Id`; legacy wrapper `llm-tools-platform` не перекрывает forwarded raw model.

- [x] Прокинуть текущую модель из форка `Open WebUI` в `OpenAPI Tool Server`.
  Комментарий: выполнено в форке `Open WebUI`: наш `OpenAPI Tool Server` получает `X-OpenWebUI-Model-Id` и `user_inputs.current_model_id`; direct smoke подтвердил `model_execution.used_model_ids=["qwen-14b-llm"]`.

Acceptance:

- Одна и та же tool logic работает с `qwen-14b-llm`, `qwen-vl-8b` и custom GGUF, если модель поддерживает нужный формат вызова.
- Инструмент не становится владельцем всей истории чата.
- История сохраняется в нативном формате `Open WebUI`.

Verification:

- `pytest backend/tests/test_agent_api_openai_compat.py -q`
- `pytest backend/tests/test_openapi_tools_api.py backend/tests/test_tool_job_store.py -q`
- live smoke: direct `analyze_equipment_deep` и `Playwright` сценарий `openwebui native deep-job equipment confirmed launch`.

### M-PREFIN.4 - Убрать служебные блоки из `assistant_message`

- [x] Отключить добавление `Timing / Quality` в текст ответа для `OpenAI-compatible` чата.
  Комментарий: выполнено на границах `/v1/chat/completions`, `OpenAPI Tool Server` completed-result и `tool_jobs` status/result; telemetry footer очищается перед отдачей в `Open WebUI`.

- [x] Оставить метрики в логах или отдельном служебном поле.
  Комментарий: `execute_orchestration` продолжает отдавать `telemetry`/`execution_metadata`; санитизация меняет только пользовательский текст OpenAI-compatible ответа.

- [x] Добавить регрессию на отсутствие footer в raw-ответе.
  Комментарий: добавлены регрессии на non-streaming, streaming compat path, completed tool result и persisted `tool_jobs` payload.

Acceptance:

- Новые ответы raw-моделей не содержат `Timing / Quality`.
- Старые сообщения могут оставаться в базе, но новые не создают повторного загрязнения.
- Метрики не теряются полностью, если они нужны для диагностики.

Verification:

- `pytest backend/tests/test_agent_api_openai_compat.py -q`
- `rg -n "Timing / Quality" /tmp/openwebui-qdrant-smoke/data` только для старых записей при smoke-проверке.

### M-PREFIN.5 - Оставить `RAG` владельцем `Open WebUI`

- [x] Не дублировать нативный `RAG` в обычном raw-чате.
  Комментарий: legacy `/v1/chat/completions` больше не ищет вложения, не загружает session docs и не включает `session_rag` для plain chat.

- [x] Для tool calls принимать файлы и источники как входной контекст.
  Комментарий: explicit `OpenAPI Tool Server` path сохраняет `document_refs`, `session_docs`, `attachments_meta` и нормализацию путей как контракт tools.

- [x] Развести knowledge chat и специализированные tools.
  Комментарий: live smoke `2026-04-27` подтвердил нативный Knowledge/RAG через `Open WebUI` -> `embedding-runtime` -> `Qdrant` -> выбранную `qwen-14b-llm`; specialized tools проверены отдельно через `analyze_equipment_deep` поверх выбранной `qwen-14b-llm`.

Acceptance:

- При обычном вопросе без tools источники отображаются нативно в `Open WebUI`.
- При tool call источник истины по файлам не расходится между `Open WebUI`, SQLite и `Qdrant`.
- Контрольные фразы из старого RAG не повторяются на бытовые сообщения без релевантного retrieval.

Verification:

- live smoke с файлом в `Open WebUI`.
- проверка `chat_message.sources` в `webui.db`.
- проверка коллекций `Qdrant`.

### M-PREFIN.6 - Починить переключение тяжёлых моделей в `UMS`

- [x] При смене тяжёлой модели выгружать текущую тяжёлую модель до проверки допуска новой.
  Комментарий: `UMS` теперь останавливает уже запущенный `gguf` / `gguf-vl` runtime до расчёта допуска новой тяжёлой модели.

- [x] Повторно считать доступную видеопамять после выгрузки.
  Комментарий: после остановки старой модели выполняется новый `_get_gpu_info()`, и только потом считается `llm_admission`.

- [x] Вернуть понятный статус загрузки и отмены.
  Комментарий: существующий `model-load-jobs` сохраняет отмену, а при переключении добавлены фазы `releasing_previous_model` и `refreshing_resources`.

- [x] Сделать прогресс `model-load-jobs` монотонным.
  Комментарий: `UMS` больше не уменьшает `bytes_loaded` / `percent` при просадке RSS-сэмпла, сохраняет последнюю скорость, а `ready` выставляет `100%` и полный `bytes_loaded`.

Acceptance:

- Переключение `qwen-14b-llm -> qwen-vl-8b` не падает только потому, что старая модель ещё занимает GPU.
- При неуспешном старте состояние `UMS` остаётся консистентным.
- Активная модель в статусе совпадает с реально запущенным процессом.

Verification:

- `pytest backend/tests/test_unified_model_server_startup.py -q`
- live check через `/status`, `/models`, `nvidia-smi`.

### M-PREFIN.7 - Нормализовать контуры запуска

- [x] Не использовать smoke `DATA_DIR` как обычный ручной контур.
  Комментарий: `smoke` стал явным `Open WebUI` data profile и больше не выбирается по умолчанию; для обычного `--openwebui-dev` используется `dev`, для Docker Compose - `stable`.

- [x] Зафиксировать отдельные профили: smoke, dev, stable local.
  Комментарий: `run_native` резолвит `dev`, `stable`, `smoke` в отдельные `DATA_DIR` и `QDRANT_COLLECTION_PREFIX`; не-stable профили разрешены только для `--openwebui-dev`.

- [x] В `run_native` передавать параметры текущего форка `Open WebUI` явно.
  Комментарий: dev-запуск форка получает явные `DATA_DIR`, `OPENAI_API_BASE_URL`, `RAG_OPENAI_API_BASE_URL`, `QDRANT_URI`, `QDRANT_COLLECTION_PREFIX`; summary печатает выбранный контур.

Acceptance:

- По команде запуска сразу понятно, какая база, какой `Qdrant` prefix и какой backend API используются.
- Smoke-контур не смешивается с рабочей историей пользователя.
- Docker compose после сборки остаётся более простым production-like путём.

Verification:

- `bash -n scripts/run_native.sh`
- `pytest backend/tests/test_runtime_launcher.py -q`
- targeted docs/config diff check.

### M-PREFIN.8 - Разделить `UMS` control-plane и runtime data-plane

- [x] Зафиксировать целевую роль `UMS` как control-plane.
  Комментарий: зафиксировано в плане и первом runtime split: `UMS` остаётся владельцем registry/lifecycle/status, а `embedding-runtime` вынесен в отдельный data-plane с health snapshot в `/status`.

- [x] Развести runtime-сервисы по типам нагрузки.
  Комментарий: первый фактический split сделан для `/v1/embeddings`; `reranker-runtime` и `document-runtime` остаются отдельными задачами `M-PREFIN.11` и `M-PREFIN.12`.

- [x] Не превращать `Open WebUI` во второй model manager.
  Комментарий: `Open WebUI` получает только OpenAI-compatible endpoint `embedding-runtime`; пути, id модели и lifecycle остаются в backend env/native launcher.

- [x] Оставить `Qdrant` только vector storage/search слоем.
  Комментарий: на этом срезе `Qdrant` не получает model-management обязанностей; metadata-защита от drift оставлена отдельным `M-PREFIN.10`.

Acceptance:

- В плане и конфигурации явно различаются control-plane и data-plane.
- `UMS` видит health/status runtime-сервисов, но горячий data-path embeddings/reranker/OCR может идти напрямую в соответствующий runtime.
- У каждого runtime есть собственный failure domain и concurrency policy.

Verification:

- ревизия `scripts/run_native.sh`, `docker-compose.yaml`, `backend/.env.example`;
- targeted tests по health/status aggregation после реализации.

### M-PREFIN.9 - Вынести `embedding-runtime` первым

- [x] Превратить текущий `st_server.py` в полноценный `embedding-runtime`.
  Комментарий: добавлен отдельный `backend/services/embedding_runtime/server.py`; старый `st_server.py` оставлен совместимым entrypoint и делегирует запуск новому runtime.

- [x] Добавить минимальные endpoints: `GET /health`, `GET /models`, `POST /v1/embeddings`, `GET /metrics`.
  Комментарий: endpoints реализованы в standalone FastAPI-приложении; `/v1/embeddings` сохраняет OpenAI-compatible формат.

- [x] Ввести отдельные env-параметры embedding runtime.
  Комментарий: в `backend/.env.example` добавлены `EMBEDDING_RUNTIME_PORT`, `EMBEDDING_MODEL_ID`, `EMBEDDING_MODEL_PATH`, `EMBEDDING_DEVICE`, `EMBEDDING_DIM`, `EMBEDDING_NORMALIZE`, `EMBEDDING_MAX_BATCH_SIZE`, `EMBEDDING_MAX_CONCURRENCY` с назначением под каждым параметром. Общий runtime-дефолт выровнен на `qwen3-embedding-0.6b`; `labse-embedding` оставлен для специализированных legal/documentation профилей.

- [x] Перенастроить `Open WebUI` RAG embeddings с `UMS` на `embedding-runtime`.
  Комментарий: `run_native --openwebui-dev` и compose env теперь указывают `RAG_OPENAI_API_BASE_URL` на порт `embedding-runtime`, а не на `UMS`; `RAG_EMBEDDING_MODEL` для общего `Open WebUI Knowledge` задан как `qwen3-embedding-0.6b`.

- [x] Сохранить `UMS` как registry/control-plane для embedding runtime.
  Комментарий: `/status` `UMS` добавляет `data_planes.embedding_runtime` с base URL, model id и health snapshot; legacy `/v1/embeddings` в `UMS` остаётся совместимым путём, но `Open WebUI` больше не использует его как hot path.

Acceptance:

- `Open WebUI` вызывает `/v1/embeddings` напрямую на `embedding-runtime`.
- Перезапуск `UMS` не должен останавливать уже запущенный embedding data-plane, если lifecycle явно не завязан на `UMS`.
- Массовая индексация документов не забивает LLM data-path.

Verification:

- `pytest backend/tests/test_unified_model_server_startup.py -q`
- новый targeted test для `embedding-runtime` `/health`, `/models`, `/v1/embeddings`;
- live smoke: загрузка файла в `Open WebUI` и проверка `Qdrant` upsert.

### M-PREFIN.10 - Описать embedding-контракт коллекций `Qdrant`

- [x] Разделить логическую базу знаний и физическую векторную коллекцию.
  Комментарий: в backend добавлена active projection для логической коллекции: она хранит физическое имя коллекции `Qdrant`, модель эмбеддинга, размерность и версию. Этот срез фиксирует контракт и не включает UI/переиндексацию.

- [x] Фиксировать embedding metadata на уровне projection.
  Комментарий: `kb_index_projections` хранит `embedding_model_id`, `embedding_dim`, `embedding_distance`, `embedding_revision`, `normalize`, `embedding_runtime_id`, `chunking_version`, `source_mode`, статус и версию. `embedding_revision` и runtime id пока опциональны, потому что runtime ещё не отдаёт устойчивую ревизию.

- [x] Проверять metadata перед upsert/search в рамках конкретной projection.
  Комментарий: `QdrantKnowledgeBaseStore` запрещает запись чанков с другой моделью/размерностью и отклоняет поиск, если `query_embedding_model_id` не совпадает с active projection. `retrieve_merged_chunks` теперь прокидывает модель запроса в store.

- [x] Поддержать управляемые коллекции с переиндексацией в новую projection.
  Комментарий: добавлен lifecycle `building -> active -> superseded`; reindex может писать чанки в отдельную physical projection через `projection_id`, а поиск остаётся на старой active projection до явного `publish_projection_sync`.

- [x] Поддержать подключение внешних `Qdrant` коллекций как attached knowledge.
  Комментарий: добавлен `attach_external_projection_sync`; external collection с явным `embedding_model_id`, `embedding_dim`, distance и chunking version становится active projection с `source_mode=attached`.

- [x] Определить поведение при отсутствующей metadata у внешней коллекции.
  Комментарий: external collection без явного embedding-профиля сохраняется как `needs_profile`; search/indexing запрещены понятной `EmbeddingProjectionMismatch` до ручной привязки профиля.

- [x] Развести несколько embedding-моделей по отдельным физическим коллекциям или named vectors.
  Комментарий: для managed reindex выбран вариант отдельных физических коллекций: `<base>__<collection_id>__v<version>`. Named vectors оставлены вне текущего среза.

- [x] Сверить текущий конфликт `labse-embedding` против `qwen3-embedding-0.6b`.
  Комментарий: решено: `qwen3-embedding-0.6b` является canonical для общего retrieval/RAG и `Open WebUI Knowledge`, потому что на тестах даёт более точное извлечение. `labse-embedding` остаётся специализированной моделью для legal/documentation инструмента или отдельного legal-профиля и не должен подставляться в общий RAG по умолчанию.

Acceptance:

- Для каждой логической базы знаний видно, какая active projection используется.
- Для каждой projection можно объяснить, каким embedding runtime, моделью, размерностью, distance и chunk recipe она сформирована.
- При смене embedding-модели управляемая база знаний получает новую projection, а не смешивает векторы.
- Внешняя `Qdrant` коллекция может быть подключена как база знаний только с явным embedding-профилем или в ограниченном режиме инспекции.
- При несовпадении embedding metadata upsert/search получает понятную ошибку, а не молча портит поиск.

Verification:

- targeted tests для managed projection: publish, reindex с новой embedding-моделью, переключение active projection;
- targeted tests для attached collection: импорт внешней коллекции с metadata, импорт без metadata, запрет поиска без профиля;
- live check metadata через `Qdrant` API;
- smoke на повторную индексацию после смены embedding model.

### M-PREFIN.11 - Вынести `reranker-runtime`

- [ ] Сначала проверить фактическую семантику `RAG_RERANKING_ENGINE` в текущем форке `Open WebUI`.
  Комментарий: пустое значение может означать local reranking, а не disabled; в коде форка нужно зафиксировать реальное поведение до изменения env.

- [ ] Сделать отдельный `reranker-runtime` с `POST /v1/rerank`, `GET /health`, `GET /models`, `GET /metrics`.
  Комментарий: reranker имеет другой профиль нагрузки: cross-encoder scoring по парам `query x document`.

- [ ] Подключить `Open WebUI` через внешний reranker endpoint.
  Комментарий: целевой профиль: `RAG_RERANKING_ENGINE=external`, `RAG_EXTERNAL_RERANKER_URL` указывает на полный `/v1/rerank` endpoint.

- [ ] Оставить `UMS` как registry/status observer для reranker runtime.
  Комментарий: `UMS` знает модель, health и placement, но не обязательно стоит в горячем rerank data-path.

Acceptance:

- Reranking явно включается или выключается через проверенный конфиг текущего форка.
- Ошибка reranker runtime деградирует retrieval/rerank слой, но не валит обычный LLM chat.
- Метрики reranker отделены от LLM и embeddings.

Verification:

- targeted tests для внешнего reranker adapter;
- live smoke с включённым и выключенным reranking;
- проверка latency на top-k rerank.

### M-PREFIN.12 - Вынести OCR/document parsing в отдельный `document-runtime`

- [ ] Не прогонять OCR/PDF parsing через `UMS`.
  Комментарий: большие PDF, картинки, таблицы, невалидные файлы и OCR имеют другой failure mode: CPU/RAM pressure, таймауты и утечки памяти.

- [ ] Спроектировать `document-runtime` endpoints: `POST /parse`, `POST /ocr`, `GET /health`, `GET /metrics`.
  Комментарий: `Docling`, `Tika` и OCR backend должны жить за отдельным retry/timeout/queue контуром.

- [ ] Зафиксировать pipeline загрузки документа.
  Комментарий: целевой поток: file upload -> `document-runtime` -> chunker -> `embedding-runtime` -> `Qdrant`.

- [ ] Развести обычный `Knowledge` ingestion и специализированные document tools.
  Комментарий: обычная индексация для поиска не равна глубокому анализу документа; глубокий анализ должен оставаться отдельным tool/job.

Acceptance:

- Сбой parsing/OCR не убивает LLM runtime и embedding runtime.
- Для больших файлов есть timeout, retry и статус выполнения.
- Результат parsing имеет стабильный contract для chunking и последующей индексации.

Verification:

- targeted tests для parse/OCR contract;
- live smoke с PDF, изображением и невалидным файлом;
- проверка RSS и timeout behavior на больших документах.

### M-PREFIN.13 - Проверить нагрузку и failure domains

- [ ] Прогнать чат во время массовой индексации.
  Комментарий: смотреть TTFT, tokens/sec и p95 latency LLM-чата.

- [ ] Прогнать несколько параллельных uploads.
  Комментарий: смотреть RSS `UMS`, RSS embedding/document runtime, VRAM, Qdrant insert latency.

- [ ] Проверить перезапуск `UMS`.
  Комментарий: после split рестарт control-plane не должен валить весь data-plane без явной lifecycle-команды.

- [ ] Проверить перезапуск `embedding-runtime`.
  Комментарий: должен деградировать RAG/ingestion, но не обычный LLM chat.

- [ ] Проверить перезапуск `document-runtime`.
  Комментарий: должен деградировать parsing/OCR и ingestion, но не обычный chat и не уже запущенный LLM inference.

Acceptance:

- У каждого runtime есть понятное degraded-состояние.
- Обычный чат не проседает критически из-за Knowledge indexing.
- Метрики позволяют отличить LLM saturation, embedding saturation, reranker saturation и document parsing saturation.

Verification:

- нагрузочный smoke script или e2e suite;
- `nvidia-smi`, process RSS, runtime `/metrics`, `Qdrant` latency;
- отчёт с p95/p99 и failure-domain выводами.

### M-PREFIN.14 - Перенести сканирование папок моделей в форк `Open WebUI`

Контекст: текущий прототип folder-browser проксирует preview папки в `UMS`. Это удобно для быстрого dev-контура, но делает форк `Open WebUI` зависимым от `UMS` даже на уровне простого просмотра файловой системы. Целевое решение: `Open WebUI` сам отвечает за browsing, saved folders, scan/preview и отмену scan; `UMS` остаётся владельцем registry, lifecycle, load/status и строгой повторной валидации выбранного candidate.

- [x] Зафиксировать `Open WebUI` backend как владельца folder browsing и preview scan.
  Комментарий: выполнено в форке `Open WebUI`: `browse-folders`, saved folders и `preview/scan` работают локально в `runtime_models.py`; `UMS` используется только для `catalog/register/load/stop/status`.

- [x] Перенести saved scan folders из `UMS` state в `Open WebUI` storage.
  Комментарий: выполнено через `PersistentConfig` `agent_navigator.runtime_models.scan_folders`; состояние хранится в конфиге `Open WebUI`, отдельно от model registry.

- [x] Сделать отменяемый scan job в `Open WebUI` backend.
  Комментарий: добавлены `scan-jobs` endpoints с `job_id`, `state/status`, progress, result и cancel flag; серверный обход проверяет отмену во время обхода.

- [x] Добавить frontend-кнопку отмены сканирования.
  Комментарий: `RuntimeModelFolderModal.svelte` запускает scan job, опрашивает статус и показывает `Cancel`; отмена не затирает последний успешный preview.

- [x] Ввести лимиты scan в `Open WebUI`.
  Комментарий: добавлены allowlist roots, нормализация путей, защита от symlink escape, `max_depth`, `max_files`, `max_duration`; скрытые папки не сканируются по умолчанию.

- [x] Перенести распознавание candidates в `Open WebUI`.
  Комментарий: локальный scan распознаёт `GGUF`, `gguf-vl`, `mmproj`, split shards; adapter и `st` остаются `unsupported` и не регистрируются как ready.

- [x] Сохранить `UMS` как строгий валидатор регистрации.
  Комментарий: регистрация по-прежнему идёт через `UMS /models/register`; frontend передаёт candidate payload, а `UMS` повторно валидирует path/runtime/mmproj/shards.

- [ ] Описать path mapping для native и Docker.
  Комментарий: отложено до этапа сборки образа. Сейчас фиксируем только принцип: в native контуре путь обычно общий; в Docker нужно будет проверить реальный volume layout после сборки, а не описывать его заранее.

- [x] Удалить старые `UMS /models/scan-folders`, `/models/browse-folders`, `/models/recommended-folders` и `/models/preview-path`.
  Комментарий: выполнено после cutover; просмотр папок и preview scan живут в backend форка `Open WebUI`, а `UMS` сохраняет только `catalog/register/load/status` и повторную валидацию registration payload.

Acceptance:

- Форк `Open WebUI` может открыть browser, просканировать папку и отменить scan без вызова `UMS`.
- Отмена scan прекращает серверный обход и не оставляет подвисший background task.
- Регистрация выбранного candidate всё равно проходит через `UMS` и получает отказ при несовпадении path/runtime/metadata.
- В Docker-контуре явно понятно, какие host/container paths должны совпадать или маппиться.

Verification:

- targeted backend tests в форке `Open WebUI` для scan job, cancel, allowlist, symlink escape, limits и candidate extraction;
- frontend/component или `Playwright` test: запуск scan, cancel, повторный scan, сохранение последнего preview;
- integration smoke: candidate найден в `Open WebUI`, зарегистрирован через `UMS`, затем модель видна в selector;
- negative smoke: candidate найден UI, но rejected by `UMS` при недоступном runtime path.

## Регрессии, которые нужно покрыть

- [x] Raw-модель без `tools` не вызывает `execute_orchestration`.
  Комментарий: покрыто `test_agent_api_openai_compat.py`; raw-ветка возвращается до legacy orchestration path.

- [x] Raw streaming не объединяет весь ответ в один SSE chunk.
  Комментарий: покрыто streaming-регрессиями и live smoke `2026-04-27`; проверяется последовательность SSE-событий и `[DONE]`.

- [x] `Timing / Quality` не попадает в `assistant_message`.
  Комментарий: покрыто на API-ответе, tool completed-result и persisted `tool_jobs` status/result; structured telemetry сохраняется отдельно.

- [x] Модели со статусом `incomplete` не попадают в `/v1/models`.
  Комментарий: покрыто фильтрацией chat-visible raw catalog и regression tests в OpenAI-compatible слое.

- [x] Tool loop использует текущий `model_id`.
  Комментарий: unit-регрессия покрывает forwarded model поверх legacy wrapper, direct smoke подтвердил `resolved_model_id=qwen-14b-llm`.

- [x] Heavy model switch выгружает старую модель перед admission новой.
  Комментарий: покрыто `test_runtime_launcher.py` и `test_unified_model_server_startup.py`; live-switch остаётся отдельной ручной проверкой перед релизом.

- [x] `Open WebUI` embeddings ходят в `embedding-runtime`, а не в `UMS`.
  Комментарий: native и compose конфигурация переключены на `embedding-runtime`; есть launcher-регрессия на `RAG_OPENAI_API_BASE_URL`.

- [x] Embedding-контракт коллекции блокирует неверный поиск/индексацию.
  Комментарий: защита работает на уровне active projection конкретной базы знаний. Новая embedding-модель создаёт отдельную building projection/физическую коллекцию, а внешняя коллекция без embedding-профиля получает `needs_profile` и не используется для поиска.

- [ ] `RAG_RERANKING_ENGINE` проверен по текущему форку перед включением external reranker.
  Комментарий: пустое значение не считать disabled без проверки кода и UI-конфига.

- [ ] Перезапуск одного runtime не валит соседние runtime.
  Комментарий: это основной критерий разделения failure domains.

- [x] Folder scan в `Open WebUI` можно отменить без зависания backend worker.
  Комментарий: добавлены cancel flag и polling-контур; targeted test покрывает выставление cancel flag, а серверный обход проверяет отмену при сканировании.

- [x] Folder preview не зависит от `UMS`, а registration по-прежнему валидируется в `UMS`.
  Комментарий: browse/preview/scan выполняются локально в форке `Open WebUI`; `register_model` по-прежнему отправляет payload в `UMS /models/register`.

## Условия завершения

- [x] В обычном `Open WebUI` списке моделей нет `llm-tools-platform`.
  Комментарий: live smoke `2026-04-27` по `/api/models` вернул реальные `qwen-14b-llm` / `qwen-vl-8b` и нативную `arena-model`; псевдомодели `llm-tools-platform` нет.

- [x] Обычный чат с `qwen-14b-llm` потоковый и без служебного footer.
  Комментарий: live smoke `2026-04-27` с `stream=true` вернул SSE `data:` chunks и `[DONE]`; служебный `Timing / Quality` не добавляется в текст ассистента.

- [x] Вызовы инструментов работают поверх выбранной модели.
  Комментарий: `Playwright` smoke `2026-04-27` прошёл для `analyze_equipment_deep`: `Open WebUI` выбрал `qwen-14b-llm`, function output содержит `job_id/status_url`, backend result использует `qwen-14b-llm`, служебный footer не попадает в user-facing текст.

- [x] `RAG` и источники остаются в нативном контуре `Open WebUI`.
  Комментарий: `OPENWEBUI_SESSION_RAG_HANDOFF` по умолчанию выключен для native и compose; конфиги общего `Open WebUI Knowledge/RAG` выровнены на внешний `embedding-runtime` с `model=qwen3-embedding-0.6b`. Предыдущий live smoke со старой embedding-моделью считается устаревшим и требует повторного прогона после переиндексации коллекции.

- [ ] Старый совместимый путь либо удалён, либо скрыт и помечен как debug-only с датой удаления.
  Комментарий: нельзя оставлять `llm-tools-platform` как постоянный пользовательский путь.

- [x] `UMS` зафиксирован как control-plane, а не обязательный data-path для embeddings/reranker/OCR.
  Комментарий: embeddings вынесены в отдельный data-plane; reranker/OCR остаются следующими профильными runtime-срезами.

- [x] `embedding-runtime` вынесен и подключён к `Open WebUI` напрямую.
  Комментарий: `run_native` стартует отдельный `embedding-runtime`, а `Open WebUI` RAG endpoint смотрит на него напрямую.

- [x] `Qdrant` защищён от silent embedding model drift.
  Комментарий: каждая логическая база знаний имеет active projection с embedding metadata; managed reindex переключается через publish, а external коллекции без metadata не используются для поиска без ручного связывания профиля.

- [x] Сканирование папок моделей живёт в форке `Open WebUI`, а `UMS` остаётся registry/load/status слоем.
  Комментарий: старые scan/browse/preview endpoints удалены из `UMS`; регистрация моделей продолжает проходить через строгую валидацию `UMS /models/register`.
