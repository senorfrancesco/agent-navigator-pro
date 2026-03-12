# TASKS - Agent Navigator Pro

## v3.0 — Hardware-Adaptive Agentic System + Chainlit (2026-02-26)

### Фаза 0 — Критические фиксы

- [x] **T3.0.1 — Багфикс MAX_CONTEXT_CHARS**
  `agent_api.py:279`: 48000 → 16000. Текущее значение переполняет контекст Qwen-14B (8192 токенов).

- [x] **T3.0.2 — Создать ветку feature/v3.0-agentic-system**

### Фаза 1 — Фундамент

- [x] **T3.1 — Фикс follow-up роутинга (new_file_count)**
  `agent_api.py:367`: `new_file_count` вместо `file_count` — считать только новые файлы.
  Тест: загрузить файл → follow-up → workflow НЕ перезапускается.

- [x] **T3.2 — Multi-turn промпт из messages[]**
  `agent_api.py`: `_build_multiturn_prompt()` — ChatML из messages[] с обрезкой до 10 сообщений.
  Тест: "Меня зовут Иван" → "Как меня зовут?" → LLM отвечает "Иван".

- [x] **T3.3 — /v1/embeddings в UMS**
  Добавить OpenAI-compatible эндпоинт для LaBSE в `unified_model_server.py`.
  Тест: `curl POST /v1/embeddings` → вектор размерности 768.

### Фаза 2 — Hardware Profiler

- [x] **T3.4 — HardwareProfiler (GPU/CPU/RAM detection)**
  Новый модуль `backend/services/hardware/profiler.py`.
  pynvml (primary), torch.cuda (fallback), psutil для CPU/RAM.
  Тест: `test_hardware_profiler.py` — определяет GPU, CPU cores, AVX2, RAM.

- [x] **T3.5 — TierSelector (Profile → TierConfig)**
  `backend/services/hardware/tier_selector.py` — маппинг железа на tier 1-4.
  VRAM budgeting: model_layers + kv_cache + overhead.
  Тест: `test_tier_selector.py` — разные SystemProfile → правильный tier.

- [x] **T3.6 — VRAM Calculator**
  `backend/services/hardware/vram_calculator.py` — расчёт gpu_layers, ctx_size.
  Тест: Qwen-14B Q4 + 8GB VRAM → partial; 16GB → -1 (all).

- [x] **T3.7 — Интеграция в UMS lifespan**
  `unified_model_server.py` → при старте: detect → select tier → load model.
  Override через .env: `TIER_OVERRIDE`, `N_GPU_LAYERS_OVERRIDE`.

### Фаза 3 — Adaptive RAG Pipeline

- [x] **T3.8 — LaBSE ONNX FP32 export**
  Экспорт LaBSE в ONNX FP32 (INT8 убран — ломает embedding space).
  Бенчмарк: ONNX 1.2-1.4x быстрее SentenceTransformers на CPU.
  Тест: `test_onnx_embeddings.py` — 14 тестов, точность delta <0.01.

- [x] **T3.9 — BM25 + Hybrid Search (RRF)**
  `backend/orchestrator/rag/retriever.py` — BM25 для русского + dense LaBSE + RRF fusion.
  Тест: `test_hybrid_search.py` — русский юр. текст, RRF > чистый dense.

- [x] **T3.10 — EmbeddingIntentClassifier**
  `backend/orchestrator/rag/classifier.py` — 5 интентов, centroid-based, без LLM.
  Тест: `test_intent_classifier.py` — accuracy >85% на 50+ тестовых запросах.

- [x] **T3.11 — AdaptiveRAGPipeline (tiered)**
  `backend/orchestrator/rag/pipeline.py` — simple/corrective/agentic/multi-agent.
  Тест: Tier 1 = 0 LLM-вызовов сверх генерации; Tier 2 = 1 classify.

- [x] **T3.12 — Legal Document Chunker**
  `backend/orchestrator/rag/chunker.py` — section-aware, overlap, sentence-boundary.
  Тест: `test_chunker.py` — не разрезает внутри нумерованных пунктов.

### Фаза 4 — Chainlit UI

- [x] **T3.13 — Chainlit PoC: базовый чат + стриминг**
  `backend/orchestrator/chainlit_app.py` — замена Open WebUI.
  Тест: `chainlit run` → стриминг чата с UMS.

- [x] **T3.14 — Chainlit: workflows + cl.Step() (детализация)**
  Compare/Equipment workflows обёрнуты в `cl.Step` для визуализации.
  Тест: загрузить 2 файла + "сравни" → видны 4 шага с прогрессом.

- [x] **T3.15 — Chainlit: Docker + auth + history**
  `Dockerfile.chainlit`, `requirements.chainlit.txt` (легковесный, без torch/llama-cpp).
  `.dockerignore` — исключает 130 ГБ моделей из build context.
  Auth: `@cl.password_auth_callback` (env-driven). Persistence: SQLAlchemy + SQLite.
  `@cl.on_chat_resume` — восстановление истории. Open WebUI → port 3001 (legacy profile).
  Тест: `docker compose up chainlit` → http://localhost:3000 → auth → chat → history.

### Фаза 5 — RAG Cleanup

- [x] **T3.16 — Удалить naive RAG из agent_api**
  Убрано: `DOC_CONTEXT_KEYWORDS`, `_should_include_doc_context()`, `_build_doc_context()`.
  Заменено на `AdaptiveRAGPipeline` — индексация при загрузке файлов, retrieve при запросе.

### Фаза 5.1 — RAG Integration в Chainlit

- [x] **T3.16.1 — create_ums_embed_fn() в ums_client.py**
  Фабрика embed_fn для AdaptiveRAGPipeline: sync HTTP к UMS `/v1/embeddings` (LaBSE).
  Probe при создании — если UMS down, возвращает `None` → BM25-only graceful degradation.

- [x] **T3.16.2 — RAG pipeline init/re-index в Chainlit on_message**
  Инициализация `AdaptiveRAGPipeline` при первой загрузке файлов.
  Переиндексация при повторной загрузке. `_get_rag_mode()` из UMS tier config или env override.

- [x] **T3.16.3 — Semantic Router intent detection**
  Двухуровневый `_detect_intent()`: EmbeddingIntentClassifier (centroid-based, ~5ms) + keyword fallback.
  `has_session_docs` параметр — если документы загружены, любой не-greeting → document_question.
  `needs_rag` флаг — greeting/general_chat не подставляют контекст документов.

- [x] **T3.16.4 — Исправить _handle_chat() (Баг 2)**
  При наличии session_docs перенаправляет на `_handle_doc_question()`.
  Убран guard `and session_docs` из routing — document_question работает без условия.

- [x] **T3.16.5 — Улучшить _handle_doc_question()**
  `asyncio.to_thread()` для sync embed_fn HTTP. try/except с fallback на naive context stuffing.
  Проверка `rag._indexed`. Mode/intent в step output.

- [x] **T3.16.6 — numpy в requirements.chainlit.txt**
  Обязателен для `retriever.py` и `classifier.py` (`import numpy as np`).

- [x] **T3.16.7 — RAG_MODE_OVERRIDE в .env.example**
  `auto` (default) → UMS tier config. `simple`/`corrective`/`agentic` → жёсткий override.

### Фаза 5.2 — RAG Quality Fixes (по результатам live-тестирования)

- [x] **T3.16.8 — Chunker: детекция спецификаций**
  `chunker.py`: добавлены `_is_specification()` и `_chunk_specification()`.
  Документы с ≥3 нумерованными позициями + bullet-атрибутами разбиваются per-item.
  Раньше «Компрессор К-250» и «Сварочный аппарат» лежали в одном чанке → RRF не находил.

- [x] **T3.16.9 — Classifier: расширенные эталоны**
  `classifier.py`: greeting += «Спасибо», «Понял», «Ясно», «Благодарю», «До свидания».
  document_question += «О чём этот документ», «Какие документы загружены», «Какие файлы ты имеешь»,
  + 8 фраз про цены/количества. general_chat: убраны «Спасибо/Понятно/Хорошо» (конфликт с greeting).

- [x] **T3.16.10 — Corrective RAG: low-margin fallback**
  `pipeline.py`: если classifier margin < 0.05 (почти случайный выбор) — делаем поиск
  даже при `needs_rag=False`. Защита от ложного пропуска RAG.

- [x] **T3.16.11 — Двойной gate для дорогих workflow**
  `chainlit_app.py`: `compare_documents` и `equipment_analysis` требуют keyword + ≥2 файлов.
  Без явного `сравни`/`различия` workflow не запускается — fallback на `document_question`.
  Фикс: «Какие документы ты имеешь?» больше не запускает тяжёлое сравнение.

- [x] **T3.16.12 — _handle_chat() доверяет Semantic Router**
  `chainlit_app.py`: убран редирект session_docs → _handle_doc_question().
  Если classifier решил `needs_rag=False` (greeting, general_chat) — отвечаем без контекста.

- [x] **T3.16.13 — E2E тест RAG pipeline**
  `test_rag_e2e.py`: standalone скрипт — 2 тестовых юр. документа, 7 вопросов, keyword evaluation.
  7/7 = 100% accuracy. Retrieve ~80ms, LLM ~1-2с.

### Фаза 6 — Production Deployment

- [ ] **T3.17 — Dockerize backend services (UMS, Doc Server, Legal Server)**
  Dockerfile'ы для каждого сервиса. UMS требует `nvidia-container-toolkit` для GPU.
  Все сервисы в `docker-compose.yaml`. Сейчас работают на хосте — контейнеризировать.

- [ ] **T3.18 — Model delivery strategy (130 GB GGUF)**
  Стратегия доставки моделей на сервер: volume mount, S3/MinIO, скрипт скачивания.
  GGUF ~130 ГБ, ST ~1.1 ГБ, ONNX ~1 ГБ. Документировать процесс деплоя.

- [ ] **T3.19 — Production secrets & auth hardening**
  Убрать admin/admin, генерация `CHAINLIT_AUTH_SECRET`.
  Docker secrets или .env вне git. Поддержка нескольких пользователей (БД вместо env).

- [ ] **T3.20 — HTTPS reverse proxy (nginx/traefik)**
  Reverse proxy перед Chainlit в docker-compose. SSL (Let's Encrypt).
  WebSocket проксирование для streaming. Rate limiting, CORS.

- [ ] **T3.21 — Healthchecks, logging, monitoring**
  Docker healthcheck для каждого контейнера (/health эндпоинты есть).
  Централизованное логирование (Docker logs / Loki).
  Мониторинг GPU (nvidia-smi), RAM, диск. Алерты при падении сервисов.

### Bugfixes v3.0 (2026-02-27)

- [x] **B3.1 — ModuleNotFoundError orchestrator в Chainlit Docker**
  `Dockerfile.chainlit`: добавлен `ENV PYTHONPATH=/app`. Абсолютные импорты `from orchestrator.workflows.*` работают.

- [x] **B3.2 — run_all.sh: пересборка при каждом запуске**
  Убран `--build --force-recreate` из запуска Chainlit. Добавлены `wait_for_service()` и `wait_for_model()`.

- [x] **B3.3 — Agent API: /health → 404**
  Проверка health изменена на `/v1/models`. curl `-sf` заменён на `-s -o /dev/null -w "%{http_code}"`.

- [x] **B3.4 — UMS: предзагрузка qwen-14b-llm при старте**
  В `lifespan` добавлен вызов `_start_server("qwen-14b-llm")`. Ожидание готовности через `/status`.

- [x] **B3.5 — Compare: 0 изменений (Docker↔Host path mismatch)**
  Chainlit сохранял файлы в `/app/orchestrator/.files/<uuid>.pdf`. Добавлены `_save_to_uploads()` и `_to_host_path()`.
  `new_files` обновляется из `session_docs` после загрузки (исправлен UUID-путь передаваемый в workflow).
  Убран mock в doc-server (`File not found` вместо фейкового текста).

- [x] **B3.6 — docker-compose: HOST_UPLOADS_DIR без хардкода**
  Убрана переменная из `.env`. Используется `${PWD}/backend/open_webui_uploads` в `docker-compose.yaml`.

- [x] **B3.7 — MCP_LEGAL_SERVER_URL неверная переменная**
  В `docker-compose.yaml` передавалась `LEGAL_SERVER_URL`, а `compare.py` читал `MCP_LEGAL_SERVER_URL`.
  Добавлена правильная переменная → Legal Server доступен из Docker.

- [x] **B3.8 — UUID вместо имён файлов в отчёте**
  Добавлены поля `name_1`/`name_2` в `CompareState`. Передаются из `chainlit_app.py`, используются в `generate_report_node`.

- [x] **B3.9 — Счётчик изменений: 0 изменено/добавлено/удалено**
  `chainlit_app.py:341-343`: поле `"status"` исправлено на `"type"` (используемое в `compare.py`).

- [x] **B3.10 — Отчёт не сохранялся в контейнере**
  `compare.py`: путь через `__file__` заменён на `os.getenv("UPLOADS_DIR")` (`/app/uploads` в Docker).

### Backlog

- [ ] **B3.11 — Убрать JSON salvage из DEBUG-POLISH**
  Статус:
  - локальная часть для `DEBUG-POLISH` выполнена: JSON-контракт и salvage parsing удалены;
  - шаг переведён на XML-контракт `<results><item id="...">...</item></results>`;
  - XML-only вариант без `id` и без адаптивного batching оказался недостаточен:
    live-прогон показал cardinality mismatch на длинных однотипных batched items;
  - текущий generic-фикс:
    - budget-based batching для polisher prompt-а;
    - XML `id`-mapping вместо позиционного сопоставления;
    - только safe cleanup явного мусора без доменных предположений о важности параметров;
  - дополнительное наблюдение из live-прогона:
    - часть деградации качества в UI шла не из самого polisher-а, а из вторичной обрезки
      `specs` в `document_analysis` report table (`[:80]`);
    - отчётный слой тоже нужно учитывать при оценке качества extraction/polish pipeline.
  - дополнительный root cause по `R760`:
    - проблема оказалась не в `DEBUG-POLISH`, а раньше, в table extraction;
    - `_parse_table_rows()` не распознавал merged ТЗ-таблицу, если `Требуемый параметр /
      Требуемое значение` находились во второй строке заголовка;
    - из-за этого включался fallback-парсер и терялись `quantity`, `value` и стартовая
      характеристика `Тип устройства - Сервер` на разрыве страниц 6-7;
    - отдельный follow-up: продолжать отслеживать page-break кейсы, где номер позиции идет
      на одной странице, а реальное `name` начинается на следующей.
  - парсер остаётся fail-fast: если отсутствует хотя бы один ожидаемый `id`, весь батч уходит
    в fallback, чтобы не допускать сдвига характеристик между позициями.
  Что осталось в работе:
  - platform-level structured output для workflow, где LLM должен возвращать массивы объектов;
  - оценить, нужен ли schema-constrained output на уровне UMS / llama-server как общая capability.
  Актуализация по коду:
  - structured output уже фактически нужен в нескольких workflow, а не только в `DEBUG-POLISH`;
  - кандидаты на общий platform-level structured output:
    - `backend/orchestrator/workflows/compare.py` — batch diff-analysis возвращает массив объектов;
    - `backend/orchestrator/workflows/equipment.py::_extract_from_single_chunk()` — извлечение списка позиций;
    - `backend/orchestrator/workflows/equipment.py::evaluate_compliance_node()` — batch-оценка соответствия;
  - `DEBUG-POLISH` не стоит тащить в общий object-based контракт только ради унификации:
    там контракт проще (`list[str]`), поэтому локальный XML-вариант остаётся предпочтительным;
  - следствие: platform-level задачу нужно вести отдельно от уже закрытого XML-рефактора polisher-а.

- [x] **B3.12 — `document_analysis` неверно маркирует legal PDF как `Договор/Контракт`**
  Live E2E на `documents/H12100110_1621890000.pdf`:
  workflow и summary корректно определяют документ как правовой акт (`Type: legal` в логах),
  но итоговый Markdown-отчёт показывает `Тип: Договор/Контракт`.
  Исправлено: `_DOC_TYPE_LABELS["legal"]` теперь рендерится как
  `Юридический / нормативный документ`.
  Повторный live E2E после пересборки `chainlit` подтвердил корректный label в UI и в
  `Report_Analysis_1772624000.md`.

- [x] **B3.13 — `equipment_analysis` падает на matching этапе из-за `404 /batch_match`**
  Live E2E на `documents/f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf` +
  `documents/Quotation_12.pdf`:
  извлечение и `DEBUG-POLISH` проходят, включая тяжёлый `R760` batch,
  но затем `POST http://host.docker.internal:8002/batch_match` возвращает `404 Not Found`.
  Из-за этого сравнение `tz_vs_smeta` заканчивается report-ом только с предупреждением,
  без матчинга и оценки соответствия.
  Исправлено:
  - `equipment.py` использует canonical endpoint `/match_batches`;
  - `mcp_legal_server.py` добавляет backward-compatible alias `/batch_match`.
  Повторный live E2E подтвердил:
  - `POST http://host.docker.internal:8002/match_batches "HTTP/1.1 200 OK"`
  - `Matched: 8 pairs`
  - полноценный `Report_Equipment_1772624340.md` вместо warning-only отчёта.

- [x] **B3.14 — Runtime warnings при стриминге / завершении Chainlit-задач**
  В live E2E RAG-сценария зафиксированы:
  - `RuntimeError: async generator ignored GeneratorExit`
  - `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`
  Пользовательский сценарий завершился успешно, но это признак некорректного cleanup/cancellation
  в streaming path и требует отдельной диагностики.
  Временный production workaround:
  - direct chat / document-question ответы в `chainlit_app.py` переведены на обычный `infer`
    без SSE-streaming;
  - streaming path оставлен для дальнейшего разбора на уровне `UMS -> httpx/httpcore`
    proxy-цепочки, где и воспроизводится основной баг cleanup.
  Инженерный вывод по логам:
  - ошибка воспроизводится даже на простом `привет`, то есть не связана с router, RAG или
    document/equipment workflow logic;
  - стек указывает на `httpcore` / `anyio` cleanup (`HTTP11ConnectionByteStream.__aiter__`,
    `aclose`, `CancelScope`), а не на бизнес-логику приложения;
  - это подтверждает, что ближайший pragmatic fix — убирать direct-chat SSE streaming,
    а не лечить prompt/router/workflow слои;
  - полноценный корневой фикс, если streaming понадобится вернуть, нужно делать в
    `Chainlit -> UMS -> llama-server` proxy-цепочке, а не в доменных workflow.
  Решение по дальнейшим работам:
  - direct-chat non-stream режим оставить как текущий production default;
  - возврат streaming делать только отдельной задачей и отдельным циклом тестирования;
  - deep fix должен быть на уровне `UMS` streaming proxy / SSE bridging, а не как точечные
    патчи в `chainlit_app.py`;
  - при возврате streaming нужен отдельный regression suite именно на cleanup/cancellation,
    а не только ручная проверка `привет`.
  Обновление по состоянию:
  - выполнен глубокий рефакторинг `UMS`/`ums_client` streaming proxy: upstream SSE теперь
    читается через queue + отдельную producer-task, чтобы stream context открывался и
    закрывался в одном task;
  - добавлены regression tests на раннее закрытие consumer;
  - полный backend suite после рефакторинга остаётся зелёным;
  - direct chat при этом сознательно оставлен в non-stream режиме как production default,
    пока не будет отдельного решения о возврате token-by-token UX поверх новой proxy-схемы.
  Статус после live-проверки:
  - smoke `привет` и `document_question` больше не воспроизводят
    `RuntimeError: async generator ignored GeneratorExit` и
    `Attempted to exit cancel scope in a different task`;
  - direct chat штатно отвечает через обычный `infer`;
  - таргетный набор `test_chainlit_streaming.py`,
    `test_unified_model_server_streaming.py` проходит.

- [x] **B3.15 — Очистка и консолидация устаревших markdown-документов**
  В корне репо накопились временные и частично устаревшие `.md` файлы, которые уже расходятся
  с текущим состоянием кода и `TASKS.md`.
  Что нужно сделать:
  - определить canonical-источники статуса и планов (`TASKS.md`, `README.md`, `AGENTS.md`);
  - удалить, архивировать или переписать устаревшие документы:
    - `FINAL_STABILIZATION_REPORT.md` — содержит шаги, которые уже выполнены
      (`UMS` semaphores, `total_pages` и др.), поэтому в текущем виде вводит в заблуждение;
    - `PLAYWRIGHT_TEST_REPORT.md` — исторический одноразовый отчёт прогона, дублируется
      Working Notes в `TASKS.md`;
    - `STABILIZATION_PLAN.md` — план, значительная часть которого уже реализована;
    - `TASKS_ANALYSIS.md` — ревизия задач от `2026-03-03`, часть выводов устарела после
      повторных live E2E и фиксов;
    - `tasks.md` — устаревший дубликат `TASKS.md`, создаёт прямой конфликт источников истины;
  - если что-то из этих файлов нужно сохранить, перевести их в формат `docs/archive/...`
    с явной пометкой, что это исторический снимок, а не актуальный operational plan.
  Решение по текущему состоянию:
  - `AGENTS.md` выглядит актуальным и полезным для репо;
  - `TASKS.md` должен оставаться единственным operational backlog / working log;
  - исторические отчёты не должны конкурировать с `TASKS.md` и `README.md`.
  Статус:
  - устаревший `tasks.md` удалён;
  - исторические markdown-документы разобраны и не используются как operational source;
  - backlog и актуальные решения закреплены в `TASKS.md`,
    документация синхронизирована через `README.md` и `AGENTS.md`.

- [ ] **B3.16 — Проверить и стабилизировать кейс `llama-server defunct` / uptime после простоя**
  В исторических стабилизационных заметках несколько раз фигурировал сценарий, где
  `llama-server` переходил в состояние `<defunct>` после тяжёлой суммаризации или простоя.
  Часть смягчающих мер уже сделана (последовательная суммаризация, semaphores в `UMS`),
  но отдельной верификации на текущей архитектуре ещё нет.
  Что нужно сделать:
  - воспроизвести сценарий длинного `document_analysis` и повторного запроса после простоя;
  - проверить, остаётся ли `llama-server` живым и отвечает ли `/status`;
  - если проблема ещё существует, решить через watchdog / healthcheck / autorestart,
    а не ручной перезапуск.

- [ ] **B3.17 — Проверить реальную эффективность prompt-cache / повторных запросов**
  В старых рабочих заметках был отдельный пункт про prompt-cache, но в текущем operational
  backlog эта тема не закреплена.
  Что нужно сделать:
  - сравнить latency и поведение при первом и повторном запросе к одному и тому же документу;
  - понять, даёт ли текущая цепочка `Chainlit -> UMS -> llama-server` реальную выгоду от кэша;
  - если кэш не даёт эффекта, определить причину:
    разные prompt-prefixes, отсутствие cache reuse, неудачный batching или сброс процесса.

- [x] **B3.18 — VRAM-aware fallback для `labse-embedding` / `st_server`**
  В live runtime после исправления direct chat streaming всплыл отдельный ресурсный дефект:
  `LaBSE` пытается стартовать на `cuda`, когда вся VRAM занята `qwen-14b-llm`, и падает
  с `CUDA out of memory`.
  Что нужно обеспечить:
  - `UMS` должен сначала пробовать `st_server` на GPU, но при неуспешном старте
    автоматически деградировать на CPU;
  - readiness/startup path должен быстро распознавать ранний exit дочернего процесса,
    а не ждать глухой timeout;
  - запуск одной и той же embedding-модели должен быть сериализован: live-логи показали,
    что без per-model startup lock параллельные `/v1/embeddings` могут одновременно пытаться
    поднять `labse-embedding`, и второй старт уходит в лишний `CUDA OOM`, даже если первый
    процесс уже успешно обслуживает запросы;
  - нужны regression tests на сценарии:
    - GPU-start fail -> CPU fallback;
    - явный `device_mode=cpu` не должен сначала пробовать `cuda`;
    - concurrent startup одного `model_id` не должен запускать второй процесс.
  Статус после реализации:
  - в `UMS` добавлены `cuda -> cpu` fallback, early-exit detection,
    per-model startup lock и stale listener cleanup по порту;
  - regression tests на startup/fallback проходят;
  - live-проверка embeddings-path подтвердила стабильный запуск `st_server`
    без повторного ложного старта и без повторного `CUDA out of memory`
    после очистки stale listener на `8093`.

- [x] **B3.19 — Cleanup Chainlit UI/runtime warnings**
  Остаточные warning-и после стабилизации backend уже не блокируют сценарии, но шумят в логах
  и маскируют реальные проблемы.
  Что нужно закрепить:
  - `ru-RU` должен обслуживаться локальными ресурсами приложения, а не fallback-ом в `en-US`;
  - `chainlit.md` и `chainlit_ru-RU.md` должны лежать в app root и попадать в контейнер;
  - `.chainlit/config.toml` не должен использовать дефолтный `accept = ["*/*"]`, который
    вызывает browser warning про invalid MIME type;
  - `.chainlit/config.toml` должен быть совместим с установленным `chainlit`:
    для `2.9.6` обязателен `[meta].generated_by`, а аудио-настройки должны жить в
    `[features.audio]`, иначе `chainlit-ui` уходит в restart loop с `config file is outdated`;
  - `Dockerfile.chainlit` должен сохранять repo-side `.chainlit`, `translations` и `public`,
    а не удалять их на build;
  - classifier pre-init при раннем старте `chainlit` должен использовать retry/info-path,
    а не warning на каждую гонку старта с `UMS`.
  Статус после live-проверки:
  - `chainlit-ui` стартует без restart loop;
  - browser console: `0 warnings / 0 errors`;
  - `GET /project/translations?language=ru-RU` -> `200`;
  - `GET /public/logo_dark.svg` и `GET /public/avatar.svg` -> `200`;
  - warning `Skipped "*/*" because it is not a valid MIME type` больше не воспроизводится.

- [x] **B3.20 — UX fallback для неоднозначного routing в Chainlit**
  Live-кейс показал, что при двух загруженных документах (`ТЗ + КП`) и двусмысленном вопросе
  semantic router может увести сценарий в `document_question`, `general_chat` или даже
  `document_analysis` по одному файлу. Ближайшее правильное решение — не угадывать дорогой
  workflow, а спрашивать пользователя.
  Что нужно закрепить:
  - ambiguity gate по `confidence`/`margin` для routing дорогих workflow;
  - deterministic pre-routing для `detect_equipment_mode(...) == tz_vs_smeta`;
  - нативный `Chainlit` fallback через `AskActionMessage` с интерактивным выбором в чате;
  - текстовый запасной путь `1/2/3/отмена`, если action timeout или пользователь не кликает.

- [x] **B3.24 — Report_Equipment: убрать обрезание колонок (hybrid output)**
  Выполнено:
  - в `generate_equipment_report_node` убраны жёсткие срезы `[:60]` и `[:80]`;
  - сохранена обзорная таблица, добавлен раздел `Полные детали по позициям`;
  - полный `reason` и `specs` выводятся без truncation;
  - добавлено safe markdown-экранирование, чтобы длинные значения не ломали таблицу.

- [x] **B3.25 — Document Question guard: не просить повторно тексты при загруженных docs**
  Выполнено:
  - усилен system prompt для `document_question` (ответ только по переданному контексту);
  - добавлен low-result fallback при `0` релевантных фрагментов;
  - добавлен post-guard: если модель просит «загрузите/предоставьте тексты» при непустых `session_docs`,
    выполняется одноразовая регенерация ответа с жёстким ограничением.

- [x] **B3.26 — Синхронизация `backend/.env` с `backend/.env.example`**
  Выполнено:
  - `backend/.env` дополнен недостающей секцией RAG;
  - добавлен `RAG_MODE_OVERRIDE="auto"` с тем же комментарием, что в `backend/.env.example`.

- [x] **B3.27 — Citation-контракт для `document_question` (inline [n] + sources + heuristic_v1)**
  Выполнено:
  - добавлен контракт ответа `answer_text + sources + confidence + answer_mode + fallback_type`;
  - внедрены inline citations `[n]` с post-validation диапазона ссылок;
  - `SourceRef` расширен: `raw_score`, `normalized_score`, `grade`, `z_score`, `char_span`;
  - policy сделана mode-aware (`simple` vs `corrective/agentic`) без единого жёсткого глобального порога;
  - добавлен retry (1 раз) + deterministic fallback без LLM при citation-fail;
  - confidence помечен явно: `confidence_method="heuristic_v1"`, `confidence_version="1"`;
  - в логах зафиксированы диагностические признаки (`mode/top_raw/citations/fallback/confidence`).
  Live-валидация:
  - after rebuild: Playwright smoke (`ТЗ + КП` -> выбор `3`) показал корректный контракт в UI:
    inline citations `[n]`, блок `Источники` (doc/chunk/span/relevance/raw), блок `Надёжность`.

- [x] **B3.29 — Context Isolation & Active Scope stabilization (Chainlit)**
  Выполнено (P0-ядро):
  - состояние сессии переведено на `documents_by_id` + `active_doc_ids` (ordered);
  - `documents_by_name` оставлен как derived index (source of truth = `documents_by_id`);
  - повторная загрузка одноимённого файла теперь создаёт новую версию (`v2`, `v3`, ...),
    а не игнорируется по имени;
  - применён Hybrid+ policy:
    - есть новые файлы в текущем сообщении -> auto-switch active set на них;
    - новых файлов нет -> active set не меняется автоматически;
  - RAG индексируется по `active_doc_ids`, добавлен in-session cache с TTL/LRU
    (`RAG_INDEX_CACHE_MAX`, `RAG_INDEX_CACHE_TTL_S`);
  - `document_question` получил doc-target resolution по имени файла в вопросе
    (например, `quotation_12`) и fail-safe fallback, если по целевому документу
    нет подтверждённых фрагментов;
  - ambiguity routing для `two_docs + low_confidence` теперь стабильно уводится в выбор
    (не только узкий keyword-gate), добавлен `route_choice_id` в state/logs;
  - compare/equipment переведены на fail-closed поведение:
    без валидного набора файлов не происходит молчаливого выбора "последних двух";
  - в UI/step output добавлена явная строка текущего контекста:
    `Активный набор: ...`.
  Локальная верификация:
  - `pytest backend/tests/test_document_analysis.py -q` -> pass (добавлены новые unit-тесты
    на versioning, ambiguity choice, target-doc resolution и ordered active scope).
  Что осталось проверить в live E2E:
  - длинная сессия `ТЗ/КП -> юрдоки -> вопрос по quotation_12`;
  - кейс с >2 активными файлами и подтверждением выбора набора.

- [x] **B3.30 — Prod-pattern для общего обращения (small-talk) без сброса document context**
  Контекст:
  - в live-сценарии после успешного workflow (`equipment_analysis`) реплика `Спасибо`
    приводит к ответу «загрузите документы», хотя `active_docs` уже непустой;
  - для production это неверное UX-поведение: social/greeting intents не должны
    сбрасывать рабочий контекст и не должны триггерить повторную загрузку файлов.
  Что внедрить:
  - ввести явный `social_intent_guard` (`greeting`, `thanks`, `ack`, `small_talk`);
  - для social intent отвечать коротко и сохранять `active_doc_ids`/`active_mode` без изменений;
  - запретить fallback «загрузите документы», если `active_docs_count > 0`;
  - оставить ask-clarification только для task-intents с низкой уверенностью/маржой;
  - в route logs добавить причину social-ветки (`social_guard`) и флаг сохранения контекста.
  Критерии готовности:
  - после `ТЗ+КП -> анализ -> "Спасибо"` система не просит повторно загружать файлы;
  - `active_doc_ids` до/после social-реплики идентичны;
  - long-session E2E не показывает ложных перезапусков workflow на social-сообщениях.
  Статус после реализации:
  - в `chainlit_app.py` добавлен строгий `social` detector (`_is_social_query`);
  - в `_get_intent_decision` добавлен ранний `social_guard` при непустом `session_docs`
    (reason=`social_guard`, без workflow/ask-choice);
  - в `_execute_intent` для social-сообщений не выполняется принудительный switch в `active_mode=chat`
    при активном документном контексте;
  - в `_handle_chat` добавлен детерминированный social-ответ без просьбы повторно загружать файлы;
  - тесты `backend/tests/test_document_analysis.py` дополнены кейсами
    `is_social_query` и `social_guard` (pass).
  Источники best practices (research):
  - LangGraph persistence/checkpointing: https://docs.langchain.com/oss/python/langgraph/persistence
  - LangGraph conditional routing: https://docs.langchain.com/oss/python/langgraph/graph-api
  - NotebookLM research notebooks:
    - Production-Ready AI Agents
    - LLM agents
    - Agent Navigator Pro: Hybrid Routing + Multi-GPU Research

- [x] **B3.31 — API Orchestration Layer + Runtime Mode Switch (`auto | chat | specialized`)**
  Контекст:
  - при смешении диалога и графовых workflow в одном потоке растёт риск ложного роутинга;
  - нужен единый прод-контур, где API/оркестратор является слоем принятия решений между UI и наборами исполнителей
    (chat, doc_qa, compare, equipment, vision).
  Архитектурное правило (обязательное):
  - UI не принимает routing/policy-решений;
  - UI только отображает состояние, прогресс и action-подсказки;
  - все решения (`route`, `executor`, `model_profile`, `fallback`, `action_required`) принимает backend-оркестратор.
  Что внедрить:
  - добавить режим выполнения в сессию/запрос: `auto`, `chat_only`, `specialized_tasks`;
  - в `chat_only` полностью запретить запуск дорогих workflow;
  - в `specialized_tasks` вести только task-роутинг с явным подтверждением при ambiguity;
  - в `auto` использовать policy по confidence/margin + ask-choice вместо автозапуска;
  - ввести capability-router: `intent -> executor -> model_profile` (а не “одна модель на всё”);
  - вынести retrieval scope в отдельную ось backend-контракта:
    `rag_scope`, `knowledge_collection_id`, `source_scope_summary`;
  - не кодировать `session_rag` / `knowledge_base_rag` внутри `runtime_mode`:
    `runtime_mode` отвечает только за chat/task policy, а не за область retrieval;
  - для текущей фазы зафиксировать pragmatic persistence ownership:
    `Chainlit SQLite` / current Chainlit data layer временно остаётся authoritative для
    `resume`, `action_required`, `state_ref`, `pending_action_id`, чтобы не раздувать scope;
  - dedicated backend-owned state store/checkpointer вынести в отдельную follow-up фазу
    `B3.31a`, а не пытаться закрыть внутри текущего execution-boundary refactor;
  - добавить в backend contract идентификаторы состояния:
    `state_ref`, `pending_action_id`;
  - убрать прямую зависимость workflow/backend-узлов от `Chainlit` runtime API
    (`cl.Step`, `chainlit.context`) через progress adapter / callback / no-op abstraction,
    чтобы headless execution и unit-тесты не падали с `ChainlitContextException`;
  - унифицировать API-контракт ответа для UI:
    `mode`, `route`, `rag_scope`, `knowledge_collection_id`, `source_scope_summary`,
    `model_profile`, `sources`, `confidence`, `trace_id`, `state_ref`,
    `pending_action_id`, `action_required`.
  Критерии готовности:
  - одинаковые запросы в одном режиме дают предсказуемый маршрут;
  - в `chat_only` workflow не стартуют;
  - в `specialized_tasks` social/greeting не ломают active scope и не сбрасывают контекст;
  - UI получает единый ответный контракт вне зависимости от выбранного исполнителя;
  - в UI отсутствуют локальные ветки бизнес-роутинга (только рендер backend-решений).
  - workflow-слой исполняется и тестируется без обязательного живого `Chainlit` context.
  Реализационное решение:
  - mutable selector для `runtime_mode` держать в `Chainlit ChatSettings`,
    а не в `ChatProfile`; `ChatProfile` остаётся coarse entrypoint.
  Follow-up / tech debt:
  - legacy `_execute_intent` / `_handle_*` path удалён из `backend/orchestrator/chainlit_app.py`;
    compatibility helper `_detect_equipment_mode` оставлен как тонкая обёртка над backend workflow helper,
    потому что на него всё ещё завязаны существующие unit-тесты и часть intent-эвристик;
  - `backend/tests/test_document_analysis.py::TestSummarizeNode` больше не зависит от живого
    `Chainlit` context: `summarize_node` переведён на optional progress step / no-op path;
  - разобраться, почему `pytest backend/tests/test_chainlit_streaming.py -q` в текущей среде
    печатает passing test output, но не завершает процесс pytest; вероятный источник —
    lingering async/task cleanup при import/reload `orchestrator.chainlit_app`.

- [ ] **B3.31a — Backend-authoritative orchestration state store**
  Контекст:
  - в `B3.31` допустимо использовать `Chainlit SQLite` как temporary authoritative persistence,
    чтобы быстро закрыть execution boundary без full rewrite persistence layer;
  - но целевая архитектура не должна считать UI/framework storage источником истины
    для execution-critical state;
  - `Chainlit` является presentation/control surface, а не владельцем workflow state;
  - нужен frontend-replaceable orchestration boundary: замена UI не должна требовать
    переписывания core execution logic.
  Проблема:
  - если `state_ref`, `pending_action_id`, `resume_state` и pending-action semantics
    живут в UI-adjacent persistence, backend orchestration остаётся привязан к storage model
    текущего фронтенда;
  - это ухудшает recoverability, усложняет смену UI и смешивает chat/thread state
    с execution state.
  Архитектурное правило:
  - всё, что нужно для resume/recovery после reconnect, UI outage, backend restart
    или замены фронтенда, должно жить в backend-owned persistence;
  - UI может инициировать и отображать workflow state, но не должен владеть им.
  Что внедрить:
  - ввести dedicated backend persistence/checkpointer как authoritative source of truth для:
    `state_ref`, `pending_action_id`, `resume_state`, execution status, checkpoint metadata,
    idempotency/correlation metadata, workflow/run identity;
  Research note (2026-03-12, Chainlit custom data layer docs):
  - `Chainlit BaseDataLayer` покрывает persistence для `users`, `feedback`, `elements`,
    `steps`, `threads`, `thread metadata/tags` и delete/list/get/update операции по ним;
  - это делает custom data layer хорошим кандидатом для replaceable chat-history/presentation store
    и для controlled Postgres migration вместо framework-default SQLite;
  - но сам по себе custom data layer не задаёт backend execution semantics для
    `pending_action_id`, workflow checkpointing, idempotent resume и orchestration recovery;
  - поэтому `Chainlit` custom data layer можно использовать как bridge или UI persistence layer,
    но не считать автоматической заменой отдельного backend-owned orchestration state store
    без явной адаптации и domain contract поверх него;
  - определить минимальную backend-owned модель состояния:
    `run_id`, `workflow_type`, `thread_id`/`conversation_id` mapping, `status`, `state_ref`,
    `pending_action_id`, `resume_state_blob`, `checkpoint_blob`, `version`,
    `created_at`, `updated_at`, `last_error`, `idempotency_key`;
  - при необходимости выделить `pending_actions` и/или `orchestration_events`
    как отдельные сущности, но не раздувать фазу до event-sourcing platform;
  - ввести repository/store abstraction (`OrchestrationStateStore` / `ExecutionStateRepository`),
    чтобы orchestration code зависел от интерфейса, а не от конкретной DB schema;
  - перевести resume/recovery logic на backend store:
    start workflow, submit user action, fetch current execution state, fetch pending action,
    resume/reconnect current run;
  - обеспечить stable mapping между UI thread/session и backend run world,
    не делая UI-thread источником истины;
  - добавить consistency protections:
    optimistic locking via `version`, idempotency for repeated action submissions,
    protection from double resume и duplicate action completion,
    atomic transition around `pending_action_id` changes.
  Storage guidance:
  - production target: Postgres-backed state store;
  - SQLite допустим только как local/dev backend store;
  - Redis не использовать как единственный authoritative resumable store без durability model.
  Что НЕ входит:
  - полная замена chat history storage;
  - перенос всех UI metadata в backend;
  - event sourcing / saga engine / distributed worker platform;
  - полный redesign orchestration domain model;
  - замена `Chainlit` на другой frontend в рамках этой же фазы.
  Migration strategy:
  - Stage 1: текущая фаза `B3.31` остаётся на `Chainlit SQLite` как pragmatic temporary authority;
  - Stage 2: backend store вводится рядом с текущим storage;
  - Stage 3: backend начинает authoritative write/read для execution-critical state;
  - Stage 4: resume/recovery переключаются на backend store;
  - Stage 5: orchestration layer перестаёт читать execution-critical state из Chainlit storage,
    а `Chainlit` остаётся presentation layer и optional chat/history layer.
  Критерии готовности:
  - backend store является authoritative для `state_ref`, `pending_action_id`, `resume_state`;
  - backend может восстановить execution без чтения Chainlit persistence;
  - UI reconnect / disconnect не приводит к потере execution-critical state;
  - workflow resume работает через backend persistence;
  - orchestration layer не зависит от Chainlit storage schema;
  - замена frontend не требует изменения core orchestration logic.
  Риски и ограничения:
  - не превращать фазу в “full orchestration platform rewrite”;
  - не переносить chat history и presentation metadata в тот же scope;
  - не переобобщать storage abstraction раньше времени;
  - временно может понадобиться compatibility mapping между Chainlit thread world
    и backend run world.

- [ ] **B3.32 — LangChain adoption strategy (точечно, без full rewrite core)**
  Контекст:
  - текущая архитектура уже держит продовый контур через `LangGraph + AdaptiveRAGPipeline + Chainlit`;
  - Open WebUI использовался как legacy UI-клиент к нашему API (`/v1/chat/completions`), а не как источник оркестрации.
  Архитектурное уточнение (2026-03-12):
  - не делать rewrite в generic `LLM + MCP tools` как замену текущему orchestration core до закрытия `B3.31`;
  - проблема проекта сейчас не в отсутствии tool protocol, а в split-brain между UI и backend decision layer;
  - `MCP` рассматривать как потенциальный adapter/protocol boundary для внешних инструментов и replaceable tool integration,
    а не как замену backend-owned routing/state/execution contract;
  - после стабилизации `B3.31/B3.31a` можно отдельно оценить selective MCP-adoption для document/legal/knowledge tools,
    если это уменьшит coupling и упростит смену UI/clients без деградации deterministic workflows.
  Решение:
  - не делать полную миграцию core-логики на “чистый LangChain”;
  - использовать LangChain точечно там, где есть измеримая выгода (retriever/reranker/evals/observability adapters);
  - сохранить доменный routing/policy/state (`active_doc_ids`, `pending-choice`, `doc/equipment/compare`) в текущем orchestrator.
  Критерии готовности:
  - оформлен ADR с trade-offs и списком допустимых LangChain-интеграций;
  - выбран 1 pilot-модуль для точечной интеграции без смены публичного API-контракта;
  - подтверждено, что user-flow и UI-контракт не ломаются.

- [ ] **B3.28 — Coverage heuristic v1.1 для `document_question`**
  Контекст:
  - в v1 сознательно не включали coverage по подпунктам запроса, чтобы не раздуть первый PR.
  Что нужно сделать:
  - добавить lightweight coverage-эвристику (покрытие ключевых аспектов multi-hop запроса);
  - включить coverage как дополнительный сигнал в policy/ confidence v2;
  - валидировать на eval-наборе и обновить пороги без ломки UI-контракта.

- [ ] **B3.33 — Session RAG vs Knowledge-Base RAG: явная продуктовая модель**
  Контекст:
  - сейчас основной RAG в проекте session-scoped и завязан на `active_doc_ids`;
  - следующий продуктовый шаг требует отдельного режима “подготовленная база знаний”,
    а не только поиска по файлам текущего чата.
  Что нужно сделать:
  - формально разделить два режима:
    - `session_rag` — ingestion и retrieval только по документам текущей сессии;
    - `knowledge_base_rag` — retrieval по постоянной подготовленной базе
      с session-overlay, если в текущем чате уже есть активные документы;
  - отразить эти режимы в UX как отдельные вкладки / chat profiles / workspace modes;
  - не смешивать их с `general_chat` и workflow-only режимами;
  - не делать для них отдельные route-ветки:
    `route=document_question` остаётся общим, меняется только retrieval contour через `rag_scope`;
  - для `knowledge_base_rag` спроектировать source registry:
    - `sources`
    - `chunks`
    - `embeddings`
    - `content_hash`
    - `index_version`
    - `embedding_model_id`
    - `retrieval_embedder_profile`
    - `chunking_version`
    - `workspace/collection scope`
  - для merged retrieval добавить provenance на уровне источника:
    - `source_origin = session | knowledge_base`
    - `source_scope_summary = session | knowledge_base | mixed`
  - явно зафиксировать merged retrieval policy:
    - отдельный candidate budget для `session` и `knowledge_base`;
    - dedup до final prompt;
    - score normalization между контурами;
    - общий rerank/quality gate после merge shortlist;
    - citation tie-break rule для дубликатов (`session` как primary, `knowledge_base` как supporting);
  - определить upload policy:
    - когда документ индексируется только в сессию;
    - когда документ попадает в постоянную базу.
  - уточнить UX-выражение режима:
    - использовать `ChatProfile` только как coarse entrypoint;
    - `session_rag` / `knowledge_base_rag` выражать через tabs / workspace mode / `rag_scope` selector,
      а не через mutable `ChatProfile`.

- [ ] **B3.34 — Retrieval eval для `LaBSE` vs `Qwen3-Embedding-0.6B`**
  Контекст:
  - `Qwen3-Embedding-0.6B` уже выиграл intent-routing eval;
  - этого недостаточно, чтобы автоматически переводить dense retrieval/RAG с `LaBSE`.
  Что нужно сделать:
  - собрать retrieval eval dataset по document QA и legal/document similarity кейсам;
  - покрыть минимум сценарии:
    - `session_only`
    - `knowledge_base_only`
    - `mixed`
    - `unanswerable`
    - `duplicate-heavy`
  - прогнать минимум:
    - `LaBSE`
    - `Qwen3-Embedding-0.6B`
  - считать:
    - `Recall@k`
    - `MRR`
    - `nDCG@k`
    - evidence hit-rate / citation usefulness
    - `source_origin` accuracy для mixed retrieval
    - answer faithfulness / groundedness
    - grounded answer quality на контрольном наборе вопросов;
  - отдельно сравнить latency / VRAM / CPU-safe поведение;
  - только после этого решать, унифицировать ли dense embedder для intent и retrieval.

- [ ] **B3.35 — Honest tiers: выравнивание терминов с реальным runtime**
  Контекст:
  - текущие названия `simple/corrective/agentic/multi-agent` сильнее, чем фактическая реализация;
  - `multi-agent` сейчас не отдельный runtime, а fallback в `agentic`;
  - `agentic` по факту ближе к iterative retrieval loop.
  Что нужно сделать:
  - зафиксировать честную интерпретацию tiers:
    - `Tier 1` — basic retrieval
    - `Tier 2` — corrective retrieval
    - `Tier 3` — iterative retrieval
    - `Tier 4` — planned multi-agent
  - привести документацию, UI-labels и внутренние описания в соответствие этому факту;
  - отдельно решить, нужен ли вообще отдельный `Tier 4` до появления реального multi-agent runtime.

- [ ] **B3.36 — Citations/evidence UX v2 для document QA**
  Контекст:
  - базовый citation-контракт уже есть, но UX должен стать ближе к grounded document answer,
    а не к “просто список источников”.
  Что нужно сделать:
  - показывать в UI:
    - `document_name`
    - `chunk_id`
    - `source_origin`
    - `page/section`, если доступны
    - excerpt
    - relevance/confidence
  - не вводить псевдоточную метрику вида “процент использованного текста”;
  - вместо этого поддержать:
    - основной источник ответа
    - supporting sources
    - `source_scope_summary` для merged retrieval
    - число использованных фрагментов;
  - подготовить path для page-aware citations при улучшении PDF metadata extraction.

- [ ] **B3.21 — Quality upgrade: отдельная embedding-модель для intent classification**
  Intent routing не обязан использовать тот же embedder, что и retrieval. Следующий этап
  качества — выделить intent classifier в отдельный контур и сравнить модели на реальном
  routing eval-наборе.
  Update 2026-03-11:
  - после boundary stabilization это становится следующим приоритетом перед UI/Ops задачами;
  - поддержать runtime mode выбора classifier через env/backend config:
    - `embedder`
    - `llm`
    - `hybrid` (`LLM + embedder fallback`);
  - до завершения eval production default временно держать как `hybrid`, а `pure llm`
    оставить как optional profile / high-accuracy experiment;
  - добавить `abstain / unsure / needs_confirmation` state для ambiguity-paths, а не forcing top-1 label;
  - для `llm`/`hybrid` использовать короткий deterministic JSON-router на той же Qwen,
    а не переносить routing назад в UI;
  - eval делать отдельно для pure-LLM и hybrid policy, с метрикой ложных срабатываний
    на дорогих workflow.
  Benchmark 2026-03-12 on local eval harness:
  - pure embedder сейчас выигрывает у pure `llm` и текущего `hybrid` policy;
  - лучший результат на текущем dataset дал `Qwen3-Embedding-0.6B`;
  - pure `qwen-14b-llm` как JSON-router дал высокий `unsure_rate` и плохую cost-weighted quality;
  - текущий `hybrid` ухудшил quality относительно лучшего pure embedder и сильно увеличил latency;
  - поэтому repo default для intent routing переводим на `embedder` + `Qwen3-Embedding-0.6B`;
  - `LaBSE` остаётся embedding-моделью для юридических документов и semantic matching;
  - значит ближайший default нельзя переключать на `llm` или `hybrid` без доработки prompt/parser/policy.
  Что нужно сделать:
  - подготовить eval harness для интентов `compare_documents`, `equipment_analysis`,
    `document_question`, `document_analysis`, `general_chat`;
  - сравнить как минимум:
    - `sentence-transformers/LaBSE` (baseline),
    - `intfloat/multilingual-e5-large-instruct`,
    - `BAAI/bge-m3`,
    - семейство `Qwen3-Embedding-*`;
    - `joeddav/xlm-roberta-large-xnli` только как optional zero-shot audit baseline
      (не как основной production router; требует отдельной зависимости `sentencepiece`);
  - мерить не только accuracy, но и false positives на дорогих workflow;
  - ввести cost-weighted routing score и per-intent FP (`compare`, `equipment`, `document_question`);
  - считать отдельно `% llm`, `% embedder_fallback`, `% unsure`, если включён hybrid policy;
  - по результатам разделить intent embedder и legal/retrieval embedder:
    - `Qwen3-Embedding-0.6B` для intent classification;
    - `LaBSE` для юридических документов и semantic matching.

- [ ] **B3.22 — Dynamic selection of models and embedders**
  Следствие будущего quality-upgrade: система должна уметь выбирать не только LLM profile,
  но и embedder profile.
  Что нужно сделать:
  - ввести понятия `llm_profile`, `retrieval_embedder_profile`, `intent_embedder_profile`;
  - поддержать безопасное переключение между ними через backend-конфиг и будущий Ops/UI слой;
  - предусмотреть профили вроде `default`, `high-accuracy-routing`, `low-vram`, `cpu-safe`.

- [ ] **B3.23 — Multi-GPU placement policy для LLM и embeddings**
  Текущая нагрузка может перекошенно ложиться на GPU 0: `llama-server` без явного `main_gpu`
  и `st_server` с дефолтным `cuda` практически приводят к использованию первой карты.
  Монитор на GPU 0 может добавлять шум, но не является главным root cause.
  Что нужно сделать:
  - проверить явное управление `main_gpu` для GGUF/LLM;
  - добавить pinning embeddings на конкретный GPU при наличии нескольких карт;
  - определить production policy:
    - LLM → multi-GPU / tensor split;
    - embeddings → отдельная карта, если возможно;
    - fallback profiles для low-VRAM и CPU-safe режимов.

- [ ] Vision-анализ изображений (Qwen-VL интеграция)
- [ ] LLM-based Intent Classification (замена keyword routing, Tier 3+)
- [ ] Agentic RAG as LangGraph Tool (RetrieveNode внутри workflows)
- [ ] VRAM Monitor + OOM recovery + dynamic fallback
- [ ] Пул портов для динамических моделей в UMS
- [ ] **Conda environment export** — зафиксировать воспроизводимое окружение `diploma_llm`:
  Export: `conda env export -n diploma_llm --no-builds > environment.yml`
  Для переноса: `conda env create -f environment.yml`
  Дополнительно: `pip list --format=freeze > backend/requirements.lock` для точных версий pip-пакетов.
  Цель — один файл `environment.yml` в корне репо для воссоздания полного окружения хоста (llama-cpp-python, onnxruntime, torch и т.д.)

### Фаза 7 — UI/Ops + Production Inference (vLLM)

- [ ] **T4.1 — Product decision: разделение User UI и Ops UI**
  Зафиксировать архитектурное решение (ADR):
  - Chainlit остаётся основным пользовательским интерфейсом для agent/workflow сценариев.
  - Open WebUI остаётся как legacy/fallback профиль.
  - Вводится отдельный Ops UI для управления моделями и мониторинга.

- [ ] **T4.2 — Chainlit UX hardening: threads/new chat/history discoverability**
  Улучшить UX истории чатов:
  - Явная кнопка/действие «Новый чат».
  - Видимый список тредов и восстановление контекста документов при resume.
  - Отдельный smoke-test для сценария: загрузка файлов → logout/login → resume.
  - добавить welcome screen / starter cards для ключевых сценариев:
    - `General Chat`
    - `Coding Assistant`
    - `Agentic`
    - `Specific Tasks`
    - `RAG Q&A`
  - starter cards не должны запускать local-routing;
    они только предзаполняют backend-facing state:
    `assistant_mode`, `runtime_mode`, `rag_scope`, `model_profile`, `prompt_profile`.
  - подготовить явное разделение пользовательских режимов:
    - `Session RAG`
    - `Knowledge Base`
    - `General Chat`

- [ ] **T4.3 — LLM Profile Selector в Chainlit (без raw model-id в UI)**
  Добавить выбор профиля инференса (например: `default-chat`, `long-context`, `legal-compare`).
  Профиль маппится на backend-конфиг (модель, ctx, temperature, device_mode).
  Убрать жёсткую привязку к `qwen-14b-llm` в пользовательском потоке.
  Уточнение по UX/архитектуре:
  - selector профиля должен быть thin UI-control над backend policy, а не вторым local-router;
  - рядом потребуется selector runtime-mode (`chat_only | auto | specialized_tasks`) и prompt-profile
    (`default-assistant | strict-grounded-doc-qa | legal-analyst | equipment-compliance`);
  - нужен отдельный `Chainlit UX control-plane` слой через `ChatSettings` tabs:
    - `Use Case`
    - `RAG`
    - `Model`
    - `Prompt`
    - `Generation`
  - в backend contract должны жить отдельные поля:
    `assistant_mode`, `runtime_mode`, `rag_scope`, `model_profile`,
    `prompt_profile`, `generation_overrides`, `custom_system_prompt`, `tool_scope`;
  - `system_prompt`, `temperature`, `top_p`, `max_tokens` должны поддерживаться
    и через код/backend profile defaults, и через UX overrides;
  - raw `system_prompt` разрешать через UX как override, но применять только через backend validation/policy,
    а не напрямую из UI state;
  - зафиксировать precedence effective config:
    1. backend hard defaults
    2. profile defaults
    3. UX overrides
    4. executor/workflow enforced overrides
    5. backend safety validation / clamping
  - UI должен показывать effective config, а не просто локально выбранное значение;
  - см. unified plan: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`.
  Правило реализации:
  - основные правки и отладка должны идти в нативном запуске `Chainlit` на хосте;
  - Docker/compose path использовать как финальный production-validation и packaging stage,
    а не как основной цикл UI-разработки.
  Follow-up после foundation-среза:
  - текущий `knowledge_base_rag` уже доступен в UX/control-plane schema, но ещё не подключён к реальному retrieval contour;
    до завершения `B3.33` это только policy/state vocabulary, не production-ready KB search.
  - `assistant_mode` / `model_profile` уже влияют на effective config и prompt/model resolution,
    но физический model-routing пока может fallback'иться на один и тот же `qwen-14b-llm`,
    если профильные env mapping'и не заданы.
  - raw/effective control-plane state пока живёт в `user_session`; для честного resume/history UX
    нужно отдельно сохранить его в thread metadata / persistence layer и восстановление при `on_chat_resume`.

- [ ] **T4.4 — UMS Model Control API (операции для Ops UI)**
  Добавить эндпоинты:
  - `GET /models` (обнаруженные модели + статус + тип + порт)
  - `POST /models/rescan` (перескан директории моделей)
  - `POST /models/activate` (активация модели/профиля)
  - `POST /models/deactivate` (остановка модели)
  - `GET /models/health` (агрегированный health по процессам)

- [ ] **T4.5 — Dynamic model registration из папки (safe mode)**
  Реализовать безопасное подключение моделей из ФС:
  - Валидация расширений (`.gguf`, `.gguf-vl`) и доступности `mmproj` для VL.
  - Защита от дублирующихся `model_id` и конфликтов путей.
  - Метаданные модели (размер, `mtime`, `hash`) для аудита.

- [ ] **T4.6 — Port pool и scheduler в UMS**
  Закрыть TODO по динамическим портам:
  - Пул портов вместо одного `dynamic_ports`.
  - Освобождение порта при остановке процесса.
  - Политика при исчерпании пула (очередь/ошибка с подсказкой).

- [ ] **T4.7 — Concurrency policy для production**
  Ввести ограничения и политику параллелизма:
  - Queue + backpressure для LLM задач.
  - Разделение `interactive`/`batch` приоритета.
  - Таймауты и cancelation для long-running workflow.

- [ ] **T4.8 — Observability stack (Prometheus + Grafana + tracing)**
  Экспортировать и визуализировать:
  - latency `p50/p95/p99`, throughput (`req/s`, `tok/s`), queue depth
  - GPU/VRAM/CPU/RAM, model start/stop events
  - Ошибки по сервисам (`doc`/`legal`/`ums`/`orchestrator`)
  Рассмотреть `Langfuse`/`OpenTelemetry` для трейсинга LLM-цепочек.

- [ ] **T4.9 — vLLM adapter в UMS (feature-flag rollout)**
  Добавить backend-адаптер инференса:
  - `INFERENCE_BACKEND=llama_cpp|vllm`
  - Проксирование OpenAI-compatible запросов в `vLLM API`
  - Поддержка `stream/non-stream`, `v1/models`, `v1/embeddings` (где применимо)

- [ ] **T4.10 — Production docker-compose profile для vLLM**
  Добавить профиль `prod-vllm`:
  - отдельный сервис `vLLM` (GPU)
  - `chainlit` + `ops-ui` + monitoring stack
  - `healthchecks`, `restart policy`, `resource limits`, `secrets`

- [ ] **T4.11 — E2E benchmark before/after migration**
  Сравнить `llama-server` vs `vLLM` на целевых сценариях:
  - `TTFT`, токены/сек, пропускная способность
  - стабильность при `N` параллельных пользователях
  - качество ответов на regression-наборе юр. кейсов

- [ ] **T4.13 — Runtime Context Budget + Preflight Profiles (вместо фиксированного MAX_CONTEXT_CHARS)**
  Коротко: лимит контекста нужно брать автоматически, но не как сырой максимум окна модели,
  а как эффективный per-request budget с защитными ограничениями.
  Почему:
  - иначе возникают OOM, деградация latency/throughput и ложные ожидания по доступному контексту;
  - в llama.cpp контекст делится между parallel slots, а в vLLM long context требует отдельных guardrails.
  Что внедрить:
  - убрать фиксированный `MAX_CONTEXT_CHARS` из runtime policy;
  - перейти на token budget: `effective_context_tokens` на запрос (с учётом backend/slot parallelism);
  - для RAG выделять безопасную долю окна (ориентир `55–65%`) под retrieved context;
  - добавить runtime cap profile для vLLM (например 16k/32k), даже если `max_model_len` больше;
  - расширить UMS/status полем `effective_context_tokens` для orchestrator;
  - добавить preflight-конфигуратор (`default | adaptive | manual`) с записью в `.env.runtime`.
  Источники (ресёрч):
  - llama-cpp-python API (`n_ctx=0` = взять из модели):
    https://llama-cpp-python.readthedocs.io/en/stable/api-reference/
  - llama.cpp README (контекст делится на слоты при `-np`):
    https://github.com/ggml-org/llama.cpp
  - vLLM OpenAI server (`--max-model-len` берётся из model config, если не задан):
    https://docs.vllm.ai/en/v0.7.0/serving/openai_compatible_server.html
  Дополнение по delivery / scripts:
  - реализация должна прийти к единому runtime/preflight script и единому launcher API;
  - основной dev-path: native `Chainlit` на хосте;
  - container path использовать как финальный production-validation слой;
  - при анализе открытых GitHub PR использовать `#7` как основной источник идей по budgeting,
    а `#4/#5/#6` считать кандидатами на закрытие как superseded после финальной реализации.

- [ ] **T4.14 — Unified runtime launcher + PR cleanup for hardware adaptation**
  Контекст:
  - вокруг preflight/runtime budgeting уже есть несколько конкурирующих GitHub PR (`#3`, `#4`, `#5`, `#6`, `#7`);
  - текущая стратегия разработки изменилась: dev должен быть native-first для Chainlit, а не Docker-first;
  - нужен единый поддерживаемый контур для hardware adaptation и запуска.
  Что сделать:
  - собрать единый launcher API для `native | container` path;
  - объединить hardware detect / profile planning / `.env.runtime` generation в одном поддерживаемом entrypoint;
  - совместить это с `effective_context_tokens` и UMS `/status`;
  - после внедрения пройтись по открытым PR и закрыть дублирующие как superseded;
  - в каждом закрытом PR оставить комментарий, что именно реализовано и где теперь находится финальная версия.
  Артефакты:
  - unified plan: `docs/plans/2026-03-11-unified-recovery-and-ui-plan.md`
  - vLLM arg docs (long context, OOM/perf риски):
    https://docs.vllm.ai/en/v0.9.1/api/vllm/engine/arg_utils.html

- [ ] **T4.12 — Security hardening для Ops UI и Model Control API**
  Ввести `RBAC` (`admin`/`operator`/`viewer`), аудит действий и ограничение опасных операций.
  Защитить endpoints управления моделями (`authN`/`authZ` + `rate limiting`).

### Working Notes

- Важные технические решения, спорные компромиссы, временные обходы и выявленный техдолг
  должны добавляться в `TASKS.md` сразу по ходу работы, а не оставаться только в диалоге.
- Если решение временное или похоже на костыль, это нужно явно помечать в `TASKS.md`
  как follow-up / backlog-задачу с желаемым целевым вариантом.

#### 2026-03-04 — Consolidation branch model

- Каноническая основная ветка репозитория переведена на `v3.0`.
- `main` удалена с remote и локально, чтобы не было второй competing default branch.
- Удалены устаревшие ветки:
  - `feature/v3.0-agentic-system`
  - `refactor/middleware-agent`
  - `codex/analyze-repo-for-testing-feasibility`
  - `codex/explain-codebase-structure-for-beginners`
- `origin/HEAD` теперь указывает на `origin/v3.0`.
- Следствие для дальнейшей работы:
  - все новые изменения базировать на `v3.0`;
  - не использовать старое имя `tech-debt/v3.0-cleanup` как рабочую ветку.

#### 2026-03-04 — Ручная E2E-валидация через Chainlit/Playwright

- `S1 RAG question` — `documents/Requirements.pdf` — `PASS`
  Запрос про гарантию и срок поставки вернул якоря `не менее 36 месяцев` и `20 рабочих дней`.
  Router: `document_question`.
- `S2 compare_documents` — `documents/H12100110_1621890000.pdf` +
  `documents/H12300274_1688590800.pdf` — `PASS`
  Получен `Report_Compare_1772620314.md`, найдено `43` различия, compare workflow прошёл до конца.
- `S3 document_analysis (legal)` — `documents/H12100110_1621890000.pdf` — `PASS with issue`
  `Report_Analysis_1772620451.md` сохранён, summary корректный, но report metadata ошибочно
  показывает `Тип: Договор/Контракт` вместо legal/нормативного акта.
- `S4 document_analysis (tz)` — `documents/Requirements.pdf` — `PASS`
  `Report_Analysis_1772620540.md` сохранён, извлечено `3` позиции, в отчёте есть `36 месяцев`
  и `20 рабочих дней`.
- `S5 equipment_analysis (tz_vs_smeta)` — `documents/f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf` +
  `documents/Quotation_12.pdf` — `PARTIAL`
  `DEBUG-POLISH` и extraction отработали корректно, включая одиночный тяжёлый batch для `R760`
  с `Тип устройства: Сервер`, но matching сломался на `404 /batch_match`, поэтому итоговый
  `Report_Equipment_1772620860.md` содержит только предупреждение без оценки соответствия.

#### 2026-03-11 — NotebookLM auth vs MCP operational note

- Локальная аутентификация NotebookLM валидна:
  - `nlm doctor` видит cookies / CSRF / account;
  - `nlm login --check` подтверждает рабочий профиль и наличие notebooks.
- При этом MCP-интеграция в текущей среде ведёт себя нестабильно:
  - `server_info` и `refresh_auth` работают;
  - `notebook_list` через MCP может возвращать пустой список;
  - `research_start` через MCP может падать с `Failed to start research — no confirmation from API`.
- Временное правило:
  - для research/query использовать CLI fallback `nlm ...`, если MCP даёт inconsistent state;
  - не считать это проблемой самих auth tokens без дополнительной проверки через CLI.

#### 2026-03-04 — Повторная E2E-валидация после фиксов `legal label`, `match_batches` и non-stream chat

- `Smoke direct chat` — `PASS`
  Сообщение `привет` отвечает штатно. В логах больше нет
  `RuntimeError: async generator ignored GeneratorExit` и
  `Attempted to exit cancel scope in a different task...` для direct-chat path.
  Текущий pragmatic default: direct chat и `document_question` идут через обычный `infer`,
  без SSE-streaming.
- `S1 RAG question` — `documents/Requirements.pdf` — `PASS`
  Запрос про гарантию и срок поставки вернул `не менее 36 месяцев` и `20 рабочих дней`.
  Router: `document_question`.
- `S2 compare_documents` — `documents/H12100110_1621890000.pdf` +
  `documents/H12300274_1688590800.pdf` — `PASS`
  Получен `Report_Compare_1772624683.md`, compare workflow прошёл до конца,
  в UI отображён отчёт о сравнении документов, найдено `38` различий в финальном report.
- `S3 document_analysis (legal)` — `documents/H12100110_1621890000.pdf` — `PASS`
  Получен `Report_Analysis_1772624000.md`.
  В UI и report metadata документ отображается как
  `Тип: Юридический / нормативный документ`.
- `S4 document_analysis (tz)` — `documents/Requirements.pdf` — `PASS`
  Получен `Report_Analysis_1772624756.md`, извлечено `3` позиции,
  в отчёте есть `20 рабочих дней` и `36 месяцев`.
- `S5 equipment_analysis (tz_vs_smeta)` — `documents/f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf` +
  `documents/Quotation_12.pdf` — `PASS`
  Matching и evaluation прошли до конца:
  - `POST /match_batches` вернул `200 OK`;
  - `Matched: 8 pairs`;
  - получен полноценный `Report_Equipment_1772624340.md`;
  - `R760` в итоговом результате остаётся корректно сопоставленным и не теряет
    `Тип устройства: Сервер`.

---

## Tech Debt — Найденные костыли (2026-03-02)

> Выявлены в ходе аудита после реального тестирования equipment workflow.
> Ответы подтверждены консультацией с NotebookLM (RAG, LLM Agents, Production-Ready AI ноутбуки).

### 🔴 Critical (баги)

- [x] **TD-1 — `__aexit__` без `await` в `_handle_compare`**
  `chainlit_app.py`: `prev_step.__aexit__(None, None, None)` — coroutine создаётся но никогда не выполняется.
  Fix: заменить на `AsyncExitStack` или корректно управлять `async with cl.Step(...)`.

### 🟠 High

- [x] **TD-2 — `INTENT_EXAMPLES` — hardcoded training data**
  `rag/classifier.py`: примеры интентов прямо в коде. Любое изменение требует деплоя.
  Fix: YAML-файл `data/intent_examples.yaml` + загрузка из vector DB (Chroma/FAISS) без перезапуска.
  Доп.: 10-15 примеров недостаточно для семантически близких интентов (document_question vs document_analysis).

- [x] **TD-3 — `index_documents` блокирует event loop**
  `chainlit_app.py::on_message`: sync вызов `rag.index_documents(...)` в async handler.
  Fix: `await asyncio.to_thread(rag.index_documents, all_texts, doc_names=all_names)`.

- [x] **TD-4 — `on_chat_resume` не восстанавливает документы**
  При возобновлении сессии восстанавливается только история сообщений, но не загруженные документы и RAG pipeline.
  Fix: Автоматический поиск файлов в UPLOADS_DIR по истории шагов и их переиндексация.

- [x] **TD-5 — `sys.path.append` в рантайме**
  В нескольких файлах (equipment.py, compare.py и др.) — `sys.path.insert/append` вместо нормального пакета.
  Fix: `pyproject.toml` + абсолютные импорты во всех модулях.

### 🟡 Medium

- [x] **TD-6 — Дублирование логики отчётов в 3 файлах**
  Логика дедупликации и сохранения отчётов скопирована в `compare.py`, `equipment.py`, `document_analysis.py`.
  Fix: `backend/orchestrator/shared/report_utils.py` — общие `save_report()`, `dedup_check()`, `format_header()`.

- [x] **TD-12 — Архитектурный переход от Regex к LLM Polisher для ТЗ**
  Парсеры ТЗ страдали от хардкода и хрупких эвристик.
  Fix: 
  1. Вынос всех правил и ключевых слов в `data/parsers_config.yaml`.
  2. Переход к сбору сырых данных (`raw_specs`) вместо фильтрации "на лету".
  3. Внедрение `LLM Polisher` — агентской ноды, превращающей сырой мусор из таблиц в структурированный технический текст.
  4. Реализация надежного Rule-based Fallback (на основе конфига) на случай сбоев LLM.

- [x] **TD-13 — Условная маршрутизация для предотвращения Over-extraction**
  Проблема "фантомных позиций": LLM извлекала компоненты (порты, диски) как отдельные товары.
  Fix: Логика Short-Circuit в узлах экстракции. Если табличный парсер (Pass 1) нашел >= 2 позиций, текстовая экстракция (Pass 2) пропускается. Это экономит время, токены и исключает мусор в отчетах.

- [x] **TD-14 — Стабилизация парсеров и API**
  Fix:
  1. Исправлена детекция ТЗ-структуры (теперь строго требуются колонки Параметр/Значение, чтобы не ломать обычные сметы).
  2. Исправлен маппинг страниц в Document Server (`page_count` -> `total_pages`).
  3. Внедрена неблокирующая инициализация RAG в Chainlit (try/except вокруг эмбеддингов).

- [ ] **TD-7 — O(N·M) reverse mapping в `match_items_node`**
  После получения matches от Legal Server обратный маппинг текст→item через двойной цикл.
  Fix: построить `{text: item}` dict заранее → O(1) lookup.

- [ ] **TD-8 — Fallback-цепочки скрывают ошибки**
  RAG упал → тихий degradation на naive stuffing. Нет видимости сколько раз система деградировала.
  Fix: WARNING-логирование + счётчики fallback-срабатываний, опционально Prometheus counters.

- [ ] **TD-9 — Magic numbers без документации**
  `threshold=0.45`, `k=60` (RRF), `max_chars=16000`, `MAX_HISTORY_MESSAGES=10`, `timeout=300.0` — без объяснения.
  Fix: именованные константы с комментариями + env var override для порогов.

### 🔵 Low

- [x] **TD-10 — Keyword routing как последний fallback (хрупко)**
  Keyword lists (`COMPARE_KEYWORDS`, `EQUIPMENT_KEYWORDS` и др.) работают, но хрупки к новым формулировкам.
  По best practice: keyword routing должен быть **первым слоем** (fast-pass), а не fallback-ом последнего уровня.

- [x] **TD-11 — Нет shared HTTP-клиента**
  Каждый вызов создаёт `httpx.AsyncClient(timeout=...)` заново. Нет connection pooling.
  Fix: `shared/http_client.py` с асинхронным синглтоном и пулом соединений.

---

## v2.3.1 — Багфиксы (2026-02-25)

### Выполнено

- [x] **Фикс стриминга UMS** — httpx клиент закрывался до начала генерации (`async with` scope bug) (2026-02-25)
- [x] **Скрипт run_all.sh** — убран auto-attach к tmux, работает в фоне (2026-02-25)
- [x] **Диагностика CUDA** — `nvidia_uvm` модуль зависал после sleep/hibernate, `rmmod + modprobe` решает (2026-02-25)

## v2.3.0 — Refactoring (2026-02-24)

### Выполнено

- [x] Определение порядка документов (старый/новый) — 4 уровня: query → filename → content date → mtime
- [x] Исправить дублирование отчётов — dedup 30s, хеш файлов, report-level dedup 60s + quality compare
- [x] Real-time стриминг ответов LLM — `async_infer_stream()` + прямой чат
- [x] Сессионный менеджер документов — on-demand контекст, intent keywords
- [x] Обновление CLAUDE.md — секция Recent Refactoring v2.3.0
- [x] Async LLM inference, Hungarian matching, batch analysis, dedup middleware
- [x] Создание скриптов: `run_all.sh`, `stop_all.sh`, `restart_all.sh`, `setup_ubuntu.sh`, `start_system_test.sh`
- [x] Фикс dedup key lifecycle — `try/finally` в async generator
- [x] Report quality dedup — замена отчёта если новый содержит больше изменений

## Session Log

- [x] **[2026-03-03 16:12]** Task #4: (без названия) — ✅ completed
- [x] **[2026-03-03 16:11]** Task #6: (без названия) — ✅ completed
- [x] **[2026-03-03 16:09]** Task #3: (без названия) — ✅ completed
- [x] **[2026-03-03 15:58]** Task #5: (без названия) — ✅ completed
- [x] **[2026-03-03 15:56]** Task #2: (без названия) — ✅ completed
- [x] **[2026-03-03 15:55]** Task #1: (без названия) — ✅ completed
- [x] **[2026-03-03 15:48]** Task #3: (без названия) — ✅ completed
- [x] **[2026-03-03 15:48]** Task #11: (без названия) — ✅ completed
- [x] **[2026-03-03 15:42]** Task #6: (без названия) — ✅ completed
- [x] **[2026-03-03 15:42]** Task #5: (без названия) — ✅ completed
- [x] **[2026-03-03 15:42]** Task #4: (без названия) — ✅ completed
- [x] **[2026-03-03 15:42]** Task #2: (без названия) — ✅ completed
- [x] **[2026-03-03 15:42]** Task #1: (без названия) — ✅ completed
