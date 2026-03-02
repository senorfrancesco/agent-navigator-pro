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

- [ ] Vision-анализ изображений (Qwen-VL интеграция)
- [ ] LLM-based Intent Classification (замена keyword routing, Tier 3+)
- [ ] Agentic RAG as LangGraph Tool (RetrieveNode внутри workflows)
- [ ] VRAM Monitor + OOM recovery + dynamic fallback
- [ ] Пул портов для динамических моделей в UMS

---

## Tech Debt — Найденные костыли (2026-03-02)

> Выявлены в ходе аудита после реального тестирования equipment workflow.
> Ответы подтверждены консультацией с NotebookLM (RAG, LLM Agents, Production-Ready AI ноутбуки).

### 🔴 Critical (баги)

- [ ] **TD-1 — `__aexit__` без `await` в `_handle_compare`**
  `chainlit_app.py`: `prev_step.__aexit__(None, None, None)` — coroutine создаётся но никогда не выполняется.
  Fix: заменить на `AsyncExitStack` или корректно управлять `async with cl.Step(...)`.

### 🟠 High

- [ ] **TD-2 — `INTENT_EXAMPLES` — hardcoded training data**
  `rag/classifier.py`: примеры интентов прямо в коде. Любое изменение требует деплоя.
  Fix: YAML-файл `data/intent_examples.yaml` + загрузка из vector DB (Chroma/FAISS) без перезапуска.
  Доп.: 10-15 примеров недостаточно для семантически близких интентов (document_question vs document_analysis).

- [ ] **TD-3 — `index_documents` блокирует event loop**
  `chainlit_app.py::on_message`: sync вызов `rag.index_documents(...)` в async handler.
  Fix: `await asyncio.to_thread(rag.index_documents, all_texts, doc_names=all_names)`.

- [ ] **TD-4 — `on_chat_resume` не восстанавливает документы**
  При возобновлении сессии восстанавливается только история сообщений, но не загруженные документы и RAG pipeline.
  Fix: LangGraph Checkpointer + Store для персистентности, или сохранять метаданные документов в SQLite data layer.

- [ ] **TD-5 — `sys.path.append` в рантайме**
  В нескольких файлах (equipment.py, compare.py и др.) — `sys.path.insert/append` вместо нормального пакета.
  Fix: `pyproject.toml` + `pip install -e .` в conda env и Dockerfile.

### 🟡 Medium

- [ ] **TD-6 — Дублирование логики отчётов в 3 файлах**
  Логика дедупликации и сохранения отчётов скопирована в `compare.py`, `equipment.py`, `document_analysis.py`.
  Fix: `backend/orchestrator/shared/report_utils.py` — общие `save_report()`, `dedup_check()`, `format_header()`.

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

- [ ] **TD-10 — Keyword routing как последний fallback (хрупко)**
  Keyword lists (`COMPARE_KEYWORDS`, `EQUIPMENT_KEYWORDS` и др.) работают, но хрупки к новым формулировкам.
  По best practice: keyword routing должен быть **первым слоем** (fast-pass), а не fallback-ом последнего уровня.

- [ ] **TD-11 — Нет shared HTTP-клиента**
  Каждый вызов создаёт `httpx.AsyncClient(timeout=...)` заново. Нет connection pooling.
  Fix: shared client с `lifespan` управлением или dependency injection.

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
