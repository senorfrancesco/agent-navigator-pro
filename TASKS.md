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

- [ ] **B3.28 — Coverage heuristic v1.1 для `document_question`**
  Контекст:
  - в v1 сознательно не включали coverage по подпунктам запроса, чтобы не раздуть первый PR.
  Что нужно сделать:
  - добавить lightweight coverage-эвристику (покрытие ключевых аспектов multi-hop запроса);
  - включить coverage как дополнительный сигнал в policy/ confidence v2;
  - валидировать на eval-наборе и обновить пороги без ломки UI-контракта.

- [ ] **B3.21 — Quality upgrade: отдельная embedding-модель для intent classification**
  Intent routing не обязан использовать тот же embedder, что и retrieval. Следующий этап
  качества — выделить intent classifier в отдельный контур и сравнить модели на реальном
  routing eval-наборе.
  NB (контракт надёжности роутинга, 2026-03-04):
  - intent routing зависит от валидного `intent_examples.yaml`;
  - недоступность или невалидность конфигурации считается явной ошибкой старта/инициализации,
    а не скрытым переходом на доменный fallback.
  Что нужно сделать:
  - подготовить eval harness для интентов `compare_documents`, `equipment_analysis`,
    `document_question`, `document_analysis`, `general_chat`;
  - сравнить как минимум:
    - `sentence-transformers/LaBSE` (baseline),
    - `intfloat/multilingual-e5-large-instruct`,
    - `BAAI/bge-m3`,
    - семейство `Qwen3-Embedding-*`;
  - мерить не только accuracy, но и false positives на дорогих workflow;
  - по результатам решить, нужен ли отдельный `intent_embedder` помимо retrieval embedder.

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

- [ ] **T4.3 — LLM Profile Selector в Chainlit (без raw model-id в UI)**
  Добавить выбор профиля инференса (например: `default-chat`, `long-context`, `legal-compare`).
  Профиль маппится на backend-конфиг (модель, ctx, temperature, device_mode).
  Убрать жёсткую привязку к `qwen-14b-llm` в пользовательском потоке.

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
  Обновлённый контракт (2026-03-04):
  - загрузка примеров теперь fail-fast (без предметного hardcoded fallback);
  - `_load_examples()` принимает только строгую структуру `intent -> non-empty list[str]`;
  - при отсутствии/битом YAML пишется `ERROR` с путём и причиной, затем поднимается
    `IntentExamplesConfigError` для явной деградации маршрутизации.

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
