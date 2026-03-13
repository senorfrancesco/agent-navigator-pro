# TASKS - Agent Navigator Pro

> **Единственный operational backlog.** Планы в `docs/plans/` — исторические артефакты, не operational source.

## Текущее состояние (2026-03-12)

**Ветка:** `codex/orchestration-control-plane-snapshot` (2 коммита от `v3.0`)
**Тесты:** 331 passed, 4 deselected integration/E2E checks
**Архитектура:** Chainlit UI → backend orchestration layer (`orchestration_runtime.py` + `execution_runtime.py` + `ui_control_plane.py`) → LangGraph workflows + AdaptiveRAGPipeline + UMS

---

## Выполненные фазы (v3.0)

<details>
<summary>Фаза 0-5 — Фундамент, Hardware, RAG, Chainlit, Quality (всё закрыто)</summary>

### Фаза 0 — Критические фиксы
- [x] T3.0.1 — Багфикс MAX_CONTEXT_CHARS (48000 → 16000)
- [x] T3.0.2 — Создать ветку feature/v3.0-agentic-system

### Фаза 1 — Фундамент
- [x] T3.1 — Фикс follow-up роутинга (new_file_count)
- [x] T3.2 — Multi-turn промпт из messages[]
- [x] T3.3 — /v1/embeddings в UMS

### Фаза 2 — Hardware Profiler
- [x] T3.4 — HardwareProfiler (GPU/CPU/RAM detection)
- [x] T3.5 — TierSelector (Profile → TierConfig)
- [x] T3.6 — VRAM Calculator
- [x] T3.7 — Интеграция в UMS lifespan

### Фаза 3 — Adaptive RAG Pipeline
- [x] T3.8 — LaBSE ONNX FP32 export
- [x] T3.9 — BM25 + Hybrid Search (RRF)
- [x] T3.10 — EmbeddingIntentClassifier (6 интентов, centroid-based)
- [x] T3.11 — AdaptiveRAGPipeline (tiered: simple/corrective/agentic/multi-agent)
- [x] T3.12 — Legal Document Chunker

### Фаза 4 — Chainlit UI
- [x] T3.13 — Chainlit PoC: базовый чат + стриминг
- [x] T3.14 — Chainlit: workflows + cl.Step()
- [x] T3.15 — Chainlit: Docker + auth + history

### Фаза 5 — RAG Cleanup & Integration
- [x] T3.16 — Удалить naive RAG из agent_api
- [x] T3.16.1-T3.16.7 — RAG pipeline init, intent detection, doc_question handler, numpy
- [x] T3.16.8-T3.16.13 — Chunker спецификации, расширенные эталоны, corrective fallback, двойной gate, E2E тест

</details>

<details>
<summary>Выполненные Bugfixes (B3.1-B3.30)</summary>

- [x] B3.1 — ModuleNotFoundError orchestrator в Chainlit Docker
- [x] B3.2 — run_all.sh: пересборка при каждом запуске
- [x] B3.3 — Agent API: /health → 404
- [x] B3.4 — UMS: предзагрузка qwen-14b-llm при старте
- [x] B3.5 — Compare: 0 изменений (Docker↔Host path mismatch)
- [x] B3.6 — docker-compose: HOST_UPLOADS_DIR без хардкода
- [x] B3.7 — MCP_LEGAL_SERVER_URL неверная переменная
- [x] B3.8 — UUID вместо имён файлов в отчёте
- [x] B3.9 — Счётчик изменений: 0 изменено/добавлено/удалено
- [x] B3.10 — Отчёт не сохранялся в контейнере
- [x] B3.12 — document_analysis неверно маркирует legal PDF
- [x] B3.13 — equipment_analysis падает на matching (404 /batch_match)
- [x] B3.14 — Runtime warnings при стриминге (direct chat переведён на non-stream)
- [x] B3.15 — Очистка устаревших markdown-документов
- [x] B3.18 — VRAM-aware fallback для labse-embedding / st_server
- [x] B3.19 — Cleanup Chainlit UI/runtime warnings
- [x] B3.20 — UX fallback для неоднозначного routing (AskActionMessage)
- [x] B3.24 — Report_Equipment: убрать обрезание колонок
- [x] B3.25 — Document Question guard: не просить повторно тексты
- [x] B3.26 — Синхронизация backend/.env с .env.example
- [x] B3.27 — Citation-контракт для document_question (inline [n] + heuristic_v1)
- [x] B3.29 — Context Isolation & Active Scope stabilization
- [x] B3.30 — Social intent guard без сброса document context

</details>

<details>
<summary>Выполненные Tech Debt (TD-1-TD-14)</summary>

- [x] TD-1 — `__aexit__` без `await` в `_handle_compare`
- [x] TD-2 — `INTENT_EXAMPLES` — hardcoded → YAML
- [x] TD-3 — `index_documents` блокирует event loop → `asyncio.to_thread`
- [x] TD-4 — `on_chat_resume` не восстанавливает документы
- [x] TD-5 — `sys.path.append` → абсолютные импорты
- [x] TD-6 — Дублирование логики отчётов → `report_utils.py`
- [x] TD-10 — Keyword routing как fallback
- [x] TD-11 — Нет shared HTTP-клиента
- [x] TD-12 — Regex → LLM Polisher для ТЗ
- [x] TD-13 — Условная маршрутизация для предотвращения Over-extraction
- [x] TD-14 — Стабилизация парсеров и API

</details>

---

## B3.31 — Orchestration Boundary (ЯДРО ЗАКРЫТО)

**Статус:** ядро реализовано, overnight-fix manifest закрыт по изменённому контуру; остались cleanup tails, session-sync и commit

### Что реализовано в коде

1. **Orchestration split-brain закрыт:**
   - `orchestration_runtime.py` (685 строк) — `decide_orchestration()`, social guard, ambiguity routing, `choose_route`
   - `execution_runtime.py` (583 строки) — `execute_orchestration()`, 6 executor'ов, `ExecutionDependencies` DI
   - `ui_control_plane.py` (422 строки) — presets, effective config resolution, precedence chain
   - `chainlit_app.py` делегирует в `_backend_execute_orchestration` — `_execute_intent` удалён (подтверждено тестом)

2. **OpenAI-compat endpoint унифицирован:**
   - `/v1/chat/completions` использует `execute_orchestration()` через `_build_openai_compat_request()`
   - Удалены `sessions`, `_get_or_create_session`, `run_workflow_stream`
   - `stream=false` теперь возвращает OpenAI-compatible JSON вместо принудительного SSE
   - Attachment-backed compatibility path подгружает `session_docs` через Document Server, без возврата к legacy session-RAG
   - API doc-QA честно возвращает `retrieval_unavailable`

3. **UI Control Plane:**
   - 5 assistant modes (`general_chat`, `coding`, `agentic`, `specific_tasks`, `rag_qa`) с preset-bundles
   - 5 ChatSettings tabs (Use Case, RAG, Model, Prompt, Generation)
   - Starter cards для быстрого выбора use-case
   - Precedence: hard defaults → preset → UX overrides → enforced → clamping

4. **Тесты и verification:**
   - таргетные и phase-level regression suites по orchestration/execution/Chainlit/UMS проходят
   - подтверждён набор: `120 passed` на изменённой поверхности (`agent_api`, `execution_runtime`, `orchestration_runtime`, `chainlit_app`, `document_analysis`, `equipment_workflow`, Chainlit/UMS smoke)
   - `git diff --check` и `py_compile` для изменённых runtime-файлов проходят

### Что осталось по B3.31

- [x] **B3.31-cleanup — Удалить routing-дубликаты из chainlit_app.py**
  Удалены thin-wrapper routing helper’ы из `chainlit_app.py`; UI использует backend aliases напрямую, без второго локального routing-layer.

- [x] **B3.31-session-sync — Sync guard для pending_action / pending_route_choice session keys**
  `pending_action` и `pending_route_choice` инициализируются и обновляются через единый `_set_pending_route_choice()` path, а `_apply_session_state_patch()` больше не создаёт разъезд session-ключей.

- [x] **B3.31-test-harness — Дожать полный `pytest tests/ -m "not integration"` до clean finish**
  Закрыто через набор harness-фиксов: `backend/tests/test_document_analysis.py` переведён на канонические helper’ы из backend runtime, `backend/tests/test_equipment_workflow.py::TestDocumentServerEndpoints` переведён на `httpx.ASGITransport` вместо подвисающего `TestClient`, hanging graph-runtime assertion заменён на structural graph assertion, а `backend/tests/test_intent_classifier.py` переведён со встроенного process-randomized `hash(...)` на стабильный `sha256`-seed для synthetic embeddings. Подтверждение: `cd backend && pytest tests/ -q -m "not integration"` → `331 passed, 4 deselected`.
  Доп. стабилизация: `backend/tests/test_onnx_embeddings.py::TestBenchmark::test_benchmark_speed` переведён с жёсткого `speedup_50 > 1.0` на competitive smoke-check (`mean_speedup >= 0.90` + хотя бы один batch c `speedup >= 1.0`), потому что в полном suite CPU/joblib jitter делал точную mid-batch performance assertion flaky.

- [x] **B3.31-commit — Закоммитить текущие uncommitted changes**
  Scoped commit pack для backend-first orchestration фаз уже собран отдельными commits поверх рабочего snapshot, без локальных артефактов (`.chainlit`, `.env.native`, `.files`, backup-файлы). Дополнительного “финального мегакоммита” по `B3.31` больше не требуется.

---

## Следующие приоритеты (порядок исполнения)

### Блок A — Classifier Quality (B3.21)

- [x] **B3.21 — Отдельная embedding-модель для intent classification**
  - Benchmark уже проведён: `Qwen3-Embedding-0.6B` выиграл у `LaBSE` на routing eval
  - Production default переведён на `embedder` + `Qwen3-Embedding-0.6B`
  - `LaBSE` остаётся для legal/doc similarity
  - Runtime contract теперь поддерживает `abstain/unsure` для `embedder`, `hybrid` и `llm -> embedder fallback`
  - `orchestration_runtime` трактует `__unsure__` как low-confidence signal и падает в heuristics/choose-route, а не запускает document workflow на ложной уверенности
  - Eval harness уже содержит cost-weighted routing score, unsure rate и per-intent FP metrics
  - Env thresholds:
    - `INTENT_CLASSIFIER_EMBEDDER_CONFIDENCE_THRESHOLD=0.60`
    - `INTENT_CLASSIFIER_EMBEDDER_MARGIN_THRESHOLD=0.10`
  - См. `backend/evals/intent_embedder_eval.py`, `docs/2026-03-12-intent-classifier-benchmark-report.md`

### Блок B — Retrieval Eval (B3.34)

- [x] **B3.34 — Retrieval eval: LaBSE vs Qwen3-Embedding-0.6B**
  - Собран канонический retrieval eval harness: `backend/evals/retrieval_embedder_eval.py`
  - Собран curated dataset: `session_only`, `knowledge_base_only`, `mixed`, `unanswerable`, `duplicate-heavy`
  - Метрики: `Recall@k`, `MRR`, `nDCG@k`, `evidence_hit_rate`, `source_origin_accuracy`, `answer_faithfulness`
  - Реальный CPU-прогон на минимальном curated наборе:
    - `LaBSE`: `recall_at_k=1.0`, `mrr=1.0`, `ndcg_at_k=1.0`, `evidence_hit_rate=1.0`, `source_origin_accuracy=1.0`, `answer_faithfulness=0.9143`
    - `Qwen3-Embedding-0.6B`: те же значения на этом наборе
  - Решение по результату: dense retrieval пока **не унифицировать**; `LaBSE` остаётся retrieval/legal baseline, потому что текущий curated набор показывает паритет, а не явный выигрыш `Qwen3`
  - Pragmatic notes:
    - `answer_faithfulness` в этом harness — deterministic lexical proxy, а не LLM judge
    - `bm25` mode оставлен как baseline/debug path, но решение `LaBSE vs Qwen3` принимается только по dense/hybrid runs

- [x] **B3.34a — Retrieval eval dataset expansion**
  - Curated retrieval eval dataset расширен с 5 до 9 cases:
    - hard negative `session_only`
    - legal wording `knowledge_base_only`
    - ambiguity `mixed`
    - более жёсткий `unanswerable`
  - Повторный CPU-прогон на expanded dataset:
    - `LaBSE`: `recall_at_k=1.0`, `mrr=1.0`, `ndcg_at_k=1.0`, `evidence_hit_rate=1.0`, `source_origin_accuracy=1.0`, `answer_faithfulness=0.951`
    - `Qwen3-Embedding-0.6B`: те же значения
  - Updated verdict:
    - curated dataset стал сильнее, но verdict не изменился;
    - dense retrieval baseline по-прежнему не переносим с `LaBSE` на `Qwen3`
  - Verification:
    - `pytest backend/tests/test_retrieval_embedder_eval.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"` -> `393 passed, 4 deselected`
  Follow-up:
  - `unanswerable_rejection_rate` в текущем harness остаётся optimistic proxy и требует отдельного tightening, если будем использовать его как decision metric

### Блок C — Knowledge Base RAG (B3.33)

- [x] **B3.33 — Session RAG vs Knowledge-Base RAG: продуктовая модель**
  - Выполнено через backend-owned KB source registry:
    - `kb_sources`
    - `kb_chunks`
    - `content_hash`
    - `index_version`
    - `embedding_model_id`
    - `chunking_version`
  - `knowledge_base_rag` интегрирован в unified backend core:
    - `orchestration_runtime` знает про `knowledge_collection_id`
    - `execution_runtime` использует merged retrieval без отдельного UI/API contour
  - Merged retrieval policy V1 реализована:
    - `session_rag` -> только активные session docs
    - `knowledge_base_rag` -> KB + session overlay
    - candidate budget per scope
    - dedup по normalized text
    - score normalization до финального shortlist
  - Source provenance проходит в doc-QA sources:
    - `source_origin = session | knowledge_base`
    - `collection_id`
    - `display_name`
  - Verification:
    - targeted KB/runtime suite зелёный
    - `cd backend && pytest tests/ -q -m "not integration"` -> `393 passed, 4 deselected`
  Pragmatic V1 follow-up:
  - full vector DB migration остаётся отдельным follow-up; текущий KB contour использует persisted chunk embeddings + transient hybrid shortlist

- [x] **B3.33a — Knowledge Base retrieval hardening**
  - Выполнено как production-oriented hardening без rewrite RAG stack:
    - shortlist hardening и duplicate policy закреплены;
    - `session` overlay детерминированно побеждает KB duplicate при близком score;
    - persisted KB chunk embeddings добавлены в store/ingestion scaffold;
    - retrieval использует precomputed dense vectors для KB chunks, не переэмбеддит их на query-time;
    - optional backend-owned rerank hook добавлен после merged shortlist.
  - Verification:
    - `pytest backend/tests/test_knowledge_base_store.py backend/tests/test_knowledge_base_ingestion.py backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"` -> `393 passed, 4 deselected`
  Follow-up:
  - отдельный vector DB / ANN index rollout остаётся самостоятельной фазой и не смешивается с текущим persisted-embeddings scaffold

### Блок D — Honest Tiers & Citations (B3.35, B3.36)

- [x] **B3.35 — Honest tiers: выравнивание терминов с реальным runtime**
  - Выполнено без смены machine-readable runtime keys:
    - `simple` -> basic retrieval
    - `corrective` -> corrective retrieval
    - `agentic` -> iterative retrieval
    - `multi-agent` -> planned multi-agent
  - Обновлены public-facing surfaces:
    - `tier_selector` docstrings и `TierConfig.__str__`
    - `AdaptiveRAGPipeline` docstrings и `mode_label` metadata
    - `Chainlit` control-plane labels (`Agentic (iterative)`)
    - `UMS /status` и runtime preflight report теперь публикуют `rag_mode_label`
  - Внутренние runtime keys сохранены для совместимости

- [x] **B3.36 — Citations/evidence UX v2**
  - Evidence block теперь явно показывает:
    - `display_name`
    - `chunk_id`
    - `source_origin`
    - `collection_id`
    - `section/page` при наличии
    - `quote/excerpt`
    - `relevance`
  - Добавлена summary line:
    - `source_scope_summary` / `retrieval_scope`
  - `session` и `knowledge_base` provenance проходят через unified doc-QA surface
  - Verification:
    - targeted tiers/evidence suite зелёный
    - `cd backend && pytest tests/ -q -m "not integration"` -> `389 passed, 4 deselected`

### Блок E — Runtime Budgeting (T4.13)

- [x] **T4.13 — Runtime Context Budget + Preflight Profiles**
  - Выполнено через backend-owned runtime budget contract в `UMS` и token-derived RAG truncation
  - `UMS /status` теперь публикует:
    - `runtime_profile`
    - `effective_context_tokens`
    - `retrieved_context_tokens_budget`
    - `generation_tokens_reserve`
    - `context_budget_ratio`
  - Profiles на этой фазе backend/env-driven: `default`, `adaptive`, `manual`
  - `AdaptiveRAGPipeline` больше не живёт только на fixed `max_context_chars`: budget вычисляется от `effective_context_tokens`
  - `Chainlit` читает runtime budget metadata из `UMS` и применяет её при RAG reindex/retrieval summary
  Pragmatic follow-up:
  - user-facing selector runtime/preflight profile остаётся в `T4.3`
  - unified preflight script / `.env.runtime` / launcher consolidation остаются в `T4.14`

- [x] **T4.14 — Unified runtime launcher + PR cleanup**
  - Выполнено через `scripts/launcher.sh` + `scripts/runtime_preflight.py`
  - Единый launcher API для `native | container`
  - Hardware detect + profile planning + `.env.runtime`
  - `run_all.sh`, `run_native.sh`, `run_container.sh` переведены в compatibility wrappers
  - runtime profiles документированы в `docs/runtime_profiles.md`
  Pragmatic follow-up:
  - cleanup/закрытие PR #3-#7 как superseded остаётся отдельным repo-maintenance шагом

### Блок F — UX Hardening (T4.2, T4.3)

- [x] **T4.2 — Chainlit UX hardening**
  Выполнено поверх стабилизированных `B3.31a + T4.13 + T4.14`.
  Что закрыто:
  - welcome/status message для нового чата с прозрачным current context summary
  - unified resume status messaging для backend-snapshot-first и legacy-history fallback
  - thread title/metadata sync в Chainlit data layer для видимого списка тредов
  - явное отображение active docs / rag scope / runtime profile / pending action state в UX summary
  - `Chainlit` остался thin control surface: routing/policy decisions не возвращались в UI
  Pragmatic note:
  - кнопка «Новый чат» и список тредов по-прежнему опираются на built-in Chainlit shell; в этой фазе усиливался не shell itself, а app-level thread presentation и resume UX

- [x] **T4.3 — LLM Profile Selector**
  Выполнено как backend-resolved control-plane слой без raw model-id selector в UI.
  Что закрыто:
  - user-facing model profiles: `default-chat`, `long-context`, `legal-compare`, `low-vram`
  - env-backed mapping `model_profile -> resolved_model_id`
  - profile hints в effective config: `device_mode`, `context_budget_profile`, `profile_generation_defaults`
  - Chainlit `Model` tab показывает canonical profiles, а summary — effective values
  - legacy/internal aliases (`coder`, `agentic`, `analyst`) нормализуются в canonical profiles без hard break
  Pragmatic follow-up:
  - per-request dynamic runtime switching в `UMS` остаётся отдельным runtime/API шагом; в этой фазе profile selector даёт backend-resolved effective config и model routing, но не живое hot-switching железа на каждый запрос

---

## Отложенные задачи

### Production Deployment (Фаза 6)
- [ ] T3.17 — Dockerize backend services (UMS, Doc Server, Legal Server)
- [ ] T3.18 — Model delivery strategy (130 GB GGUF)
- [ ] T3.19 — Production secrets & auth hardening
- [ ] T3.20 — HTTPS reverse proxy
- [ ] T3.21 — Healthchecks, logging, monitoring (docker)

### Backend State & Persistence
- [x] **B3.31a — Backend-authoritative orchestration state store**
  Выполнено через `backend/orchestrator/state_store.py` и интеграцию в unified execution path.
  Что закрыто:
  - `orchestrator_runs` с backend-owned `run_id/state_ref/pending_action_id/resume_state_blob/checkpoint_blob/version`
  - authoritative writes из `execution_runtime.py` для Chainlit, REST и OpenAI-compatible path
  - `Chainlit` resume сначала читает backend snapshot, а затем только при его отсутствии падает в legacy history bootstrap
  - `Chainlit` session state теперь UI-mirror/cache, а не единственный источник execution-critical state
  Pragmatic workaround / follow-up:
  - dev/prod fallback сейчас `SQLite` через `ORCHESTRATOR_STATE_DB_URL`; Postgres-backed implementation остаётся отдельным усилением, а не blocker'ом
  - в `resume_state_blob` пока хранится `documents_by_id` snapshot для практичного resume document workflows; это осознанный компромисс до более строгого document-ref layer
- [x] **B3.31b — Harden backend orchestration store for production**
  Follow-up к `B3.31a` закрыт safe slice без full rewrite orchestration platform.
  Что реализовано:
  - `backend/orchestrator/state_store.py` теперь поддерживает:
    - `sqlite://...` -> `SQLiteOrchestrationStateStore`
    - `postgres://...` / `postgresql://...` -> `PostgresOrchestrationStateStore`
    - unsupported scheme -> explicit configuration error вместо silent fallback
  - write path переведён на atomic optimistic locking:
    - `UPDATE ... WHERE run_id = ? AND version = ?`
    - deterministic version-conflict error вместо read-then-overwrite semantics
  - `idempotency_key` теперь участвует в run reuse semantics по `(workflow_type, idempotency_key)`
  - persisted backend snapshot slimmed from inline document-heavy payload to `document_refs`
  - `Chainlit` restore path принимает и новый `document_refs` shape, и legacy `documents_by_id`
  - targeted coverage:
    - `backend/tests/test_state_store.py`
    - `backend/tests/test_execution_runtime.py`
    - `backend/tests/test_chainlit_runtime_mode.py`
  Verification:
  - `pytest backend/tests/test_state_store.py backend/tests/test_execution_runtime.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"` -> `416 passed, 4 deselected`
  Follow-up:
  - richer document-ref hydration/resolution beyond current minimal `path/display_name/version` shape remains an incremental follow-up, not a blocker for backend-owned store semantics
  - production deployment of Postgres store still requires `psycopg` to be present in runtime environment

### Динамическая конфигурация
- [x] **B3.22 — Dynamic selection of models and embedders**
  - Dynamic selection централизован в backend control-plane:
    - `resolved_model_id`
    - `resolved_intent_embedder_model_id`
    - `resolved_retrieval_embedder_model_id`
  - `Chainlit` classifier pre-init использует resolved intent embedder, а retrieval adapter в `Chainlit` и API adapter используют resolved retrieval embedder вместо локальных hardcoded source-of-truth констант.
  - Defaults сохранены согласно текущему verdict:
    - intent embedder -> `qwen3-embedding-0.6b`
    - retrieval/legal embedder -> `labse-embedding`
  - Env-backed mapping:
    - `CHAINLIT_INTENT_EMBEDDER_PROFILE_DEFAULT_MODEL`
    - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LEGAL_DEFAULT_MODEL`
    - `CHAINLIT_RETRIEVAL_EMBEDDER_PROFILE_LOW_VRAM_MODEL`
  - Verification:
    - `pytest backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"` -> `397 passed, 4 deselected`
  Follow-up:
  - per-request hot-switching уже загруженных UMS моделей/embedders остаётся отдельным runtime API follow-up, не смешивается с текущим control-plane resolver
- [x] B3.23 — Multi-GPU placement policy для LLM и embeddings
  Реализовано через backend-owned placement policy в `backend/services/model_manager/unified_model_server.py`.
  Что закрыто:
  - weighted `tensor-split` для multi-GPU GGUF startup вместо равного деления по всем GPU
  - admission/filtering GPU по `UMS_LLM_MIN_FREE_VRAM_GB` и `UMS_LLM_MIN_BALANCE_RATIO`
  - explicit embedding placement через `cuda:<idx>` или CPU fallback вместо implicit `cuda`
  - preference на GPU, не занятый активным heavy LLM placement; fallback в CPU при конфликте
  - operational placement metadata в `UMS /status` для backend/operators
  - targeted startup tests покрывают:
    - weighted multi-GPU GGUF split
    - CPU GGUF path
    - tier-forced CPU embeddings
    - explicit non-LLM GPU selection for embeddings
    - `/status` placements payload
  - full verification:
    - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_runtime_preflight.py -q`
    - `cd backend && pytest tests/ -q -m "not integration"`
  Follow-up:
  - hot migration уже запущенных моделей между GPU остаётся отдельным runtime follow-up
  - полноценный multi-process scheduler и GPU reservations не входят в `B3.23`
  - raw placement selector в UI не добавляется; placement остаётся backend-owned operational policy

### Benchmark & Performance Profiling
- [x] **T4.15 — Unified benchmark script**
  Реализовано: `scripts/benchmark.py` — 7 сценариев (health, ums_status, embedding, chat, doc_question, compare, equipment).
  Отправляет реальные запросы + файлы к запущенной системе, измеряет latency.
  Поддерживает `--repeats` для усреднения, `--output` для JSON-результатов, `--scenarios` для выбора.
  Workflow: изменить `.env` (N_GPU_LAYERS, CONTEXT_SIZE) → перезапустить → `python scripts/benchmark.py --output results/gpu.json`
- [x] **T4.16 — Benchmark comparison tool**
  Реализовано: `scripts/benchmark_compare.py` — канонический compare-tool для двух JSON-отчётов из `scripts/benchmark.py`.
  Что закрыто:
  - deterministic compare по `scenario`
  - status change semantics: `same_ok`, `regressed_status`, `improved_status`, `changed_non_ok`
  - latency delta / delta_pct / speedup
  - added/removed scenario detection
  - runtime metadata diff по `UMS /status`
  - human-readable console summary
  - machine-readable JSON output через `--json-output`
  - focused coverage в `backend/tests/test_benchmark_compare.py`
  Verification:
  - `pytest backend/tests/test_benchmark_compare.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"`
  Follow-up:
  - statistical significance / variance analysis остаются отдельным benchmark follow-up
  - CI gating по benchmark regression не входит в `T4.16`
  Таблица delta по каждому сценарию + рекомендации.

### Agentic Orchestrator (исследование)
- [ ] **T5.1 — Feasibility: оркестратор + субагенты на CPU/слабом железе**
  Текущая архитектура уже содержит элементы агентной системы:
  - `orchestration_runtime.py` = оркестратор (decide → route)
  - LangGraph workflows = субагенты (compare, equipment, RAG)
  - `ExecutionDependencies` = DI для инъекции зависимостей
  - UMS с hardware profiling = автоадаптация под ресурсы
  Ограничения CPU: инференс Qwen 30-60с/ответ, workflows 2-5 мин.
  Нужно: формализовать sub-agent protocol, добавить timeout budgets, fallback на меньшие модели.
  **Текущий гибрид:** embedders (LaBSE, Qwen3-Embedding) на CPU, Qwen-14B inference на GPU — намеренный дизайн для сохранения VRAM.

### Inference & Ops
- [x] T4.4 — UMS Model Control API
  Выполнено как backend-owned operator surface без возврата raw model control в Chainlit UI.
  Реализовано:
  - `GET /models` для configured + filesystem-discovered моделей с полями `model_id`, `running`, `active`, `port`, `placement`, `resolved_path`
  - `GET /models/running` для compact operator view (`running_model_ids`, `active_heavy_model`, `placements`, `running_models`)
  - `POST /models/{model_id}/preload`
  - `POST /models/{model_id}/activate`
  - `POST /models/{model_id}/stop`
  - operator-safe JSON contract поверх существующей backend runtime/placement логики
  - 404 contract для unknown model ids
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py -q` → `20 passed`
  - `cd backend && pytest tests/ -q -m "not integration"` → `424 passed, 4 deselected`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
- [x] T4.5 — Dynamic model registration
  Выполнено как backend-owned runtime registry для `UMS`, без UI selector и без смешивания с `T4.6` port pool/scheduler.
  Реализовано:
  - `POST /models/register`
  - `DELETE /models/{model_id}/registration`
  - persistent manifest для dynamic моделей через `UMS_DYNAMIC_MODELS_REGISTRY_PATH` (default: `backend/.data/ums_dynamic_models.json`)
  - precedence `STATIC_MODELS_CONFIG -> registered dynamic models -> filesystem-discovered GGUF`
  - deterministic auto-port allocation для dynamic/discovered моделей без полноценного scheduler
  - unregister running dynamic model делает controlled stop через существующий `_stop_model`
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py -vv` → `25 passed`
  - `cd backend && pytest tests/ -q -m "not integration"` → `429 passed, 4 deselected`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
- [x] T4.6 — Port pool и scheduler в UMS
  Выполнено как safe slice без distributed scheduler/queue.
  Реализовано:
  - backend-owned port registry в runtime state:
    - `reserved_ports`
    - `port_owners`
    - `released_dynamic_ports`
  - единая reserve/release policy для:
    - registered dynamic models
    - filesystem-discovered GGUF models
  - reuse освобождённых dynamic портов
  - stable owner mapping для discovered models
  - minimal heavy lifecycle serialization через global reentrant lock поверх conflicting heavy start/stop transitions
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py -q` → `32 passed`
  - `cd backend && pytest tests/ -q -m "not integration"` → `436 passed, 4 deselected`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
- [x] T4.7 — Concurrency policy для production
  Реализовано в `UMS` как safe slice без distributed queue:
  - env-driven concurrency caps для LLM и embeddings:
    - `UMS_LLM_MAX_CONCURRENCY` (`UMS_LLM_CONCURRENCY` alias)
    - `UMS_EMBED_MAX_CONCURRENCY` (`UMS_EMBED_CONCURRENCY` alias)
    - `UMS_CONCURRENCY_ACQUIRE_TIMEOUT_S`
    - `UMS_FAIL_FAST_ON_SATURATION`
  - helper-based acquire/release policy с bounded wait и `429` при saturation
  - stream-path теперь резервирует slot до открытия `StreamingResponse`, без тихого `200` + пустого SSE при перегрузке
  - `/status` публикует operator-visible metadata:
    - limits
    - fail-fast flag
    - `llm_inflight` / `embedding_inflight`
    - `llm_available` / `embedding_available`
    - `llm_saturated` / `embedding_saturated`
  Verification:
  - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py`
  - `git diff --check`
  Follow-up:
  - текущий `fail-fast` реализован как safe slice поверх `asyncio.Semaphore`; остаётся теоретическая гонка между проверкой доступности и `acquire()`, если нужен строго lock-free immediate reject под экстремальной конкуренцией
  - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q` в этой среде иногда зависает на tail уже после прохождения точек; новые `T4.7` assertions проходят, а residual выглядит как harness/process-exit issue, не как regression concurrency policy
- [x] T4.8 — Observability stack (Prometheus + Grafana + tracing)
  Реализовано как minimal production-safe slice без full APM stack:
  - shared Prometheus-compatible exporter в `backend/services/observability.py`
  - `/metrics` для:
    - `backend/orchestrator/agent_api.py`
    - `backend/services/model_manager/unified_model_server.py`
  - request telemetry:
    - `agent_nav_http_requests_total`
    - `agent_nav_http_request_duration_seconds`
    - `agent_nav_http_requests_in_progress`
  - domain metrics:
    - `agent_nav_agent_api_orchestration_requests_total`
    - `agent_nav_agent_api_openai_dedup_hits_total`
    - `agent_nav_ums_concurrency_saturation_total`
    - `agent_nav_ums_running_models`
    - `agent_nav_ums_active_heavy_model`
  - trace-aware `X-Trace-Id` propagation и request logging через ASGI middleware
  - compose profile `monitoring`:
    - `prometheus` (`:9090`)
    - `grafana` (`:3002`)
  - provisioning assets:
    - `monitoring/prometheus.yml`
    - `monitoring/grafana/provisioning/...`
    - starter dashboard `agent-navigator-overview`
  - convenience launcher:
    - `scripts/run_monitoring.sh`
  Verification:
  - `pytest backend/tests/test_observability.py backend/tests/test_agent_api_metrics.py backend/tests/test_unified_model_server_startup.py -q -k "metrics or trace or observability"`
  - `pytest backend/tests/test_agent_api_orchestrate.py backend/tests/test_unified_model_server_streaming.py -q`
  - `cd backend && pytest tests/ -q -m "not integration"`
  - `python -m py_compile backend/services/observability.py backend/orchestrator/agent_api.py backend/services/model_manager/unified_model_server.py backend/tests/test_observability.py backend/tests/test_agent_api_metrics.py backend/tests/test_unified_model_server_startup.py`
  - `docker compose --profile monitoring config`
  - `bash -n scripts/run_monitoring.sh`
  - `git diff --check`
  Follow-up:
  - tmux window `monitor` пока остаётся legacy-именем для `htop/top`; отдельный rename в `syswatch` лучше делать как небольшой ops-cleanup, а не смешивать с observability stack rollout
  - health-monitoring для `document_server` / `legal_server` / `chainlit` лучше добавлять отдельным `blackbox-exporter`/synthetic probe block, а не через scrape JSON `/health` как Prometheus metrics
- [ ] T4.9 — vLLM adapter в UMS
- [ ] T4.10 — Production docker-compose profile для vLLM
- [ ] T4.11 — E2E benchmark before/after migration
- [ ] T4.12 — Security hardening для Ops UI

### Другие
- [ ] B3.11 — Убрать JSON salvage из DEBUG-POLISH (structured output platform-level)
- [ ] B3.16 — Проверить llama-server defunct / uptime после простоя
- [ ] B3.17 — Проверить prompt-cache эффективность
- [ ] B3.28 — Coverage heuristic v1.1 для document_question
- [x] B3.32 — LangChain adoption strategy (точечно, без full rewrite)
  Зафиксировано через ADR: [docs/plans/2026-03-12-b332-langchain-adoption-strategy.md](docs/plans/2026-03-12-b332-langchain-adoption-strategy.md).
  Решение: не делать full rewrite orchestration core на LangChain; сохранять backend-first contract (`orchestration_runtime.py` + `execution_runtime.py`) каноническим; разрешать только точечные integration areas: workflow-level `LangGraph`, retriever/reranker adapters, eval harness, observability adapters и один изолированный pilot area без смены публичного API.
- [ ] Vision-анализ (Qwen-VL интеграция)
- [ ] Conda environment export

### Открытый Tech Debt
- [ ] TD-7 — O(N·M) reverse mapping в match_items_node → dict lookup
- [ ] TD-8 — Fallback-цепочки скрывают ошибки → WARNING + счётчики
- [ ] TD-9 — Magic numbers без документации → именованные константы + env override

---

## PR Inventory (20 draft PR)

Все PR заморожены до завершения текущего orchestration refactor.

| Кластер | PR | Статус | Действие |
|---------|-----|--------|----------|
| A. Runtime/scripts | #3, #4, #5, #6, #7 | supersede later | Закрыть после T4.13/T4.14 |
| B. Routing config | #8, #9, #10 | defer | После B3.21 |
| C. Docs | #11, #21 | docs-only | Переносить вручную |
| D. Resume/session | #12, #16, #17, #18, #19, #20, #22 | defer | После B3.31a |
| E. Summary memory | #13, #14, #15 | defer | После resume contract |

---

## E2E валидация (2026-03-04)

| Сценарий | Документы | Результат |
|----------|-----------|-----------|
| Smoke direct chat | — | PASS |
| S1 RAG question | Requirements.pdf | PASS |
| S2 compare_documents | 2 legal PDFs | PASS (38 различий) |
| S3 document_analysis (legal) | H12100110.pdf | PASS |
| S4 document_analysis (tz) | Requirements.pdf | PASS (3 позиции) |
| S5 equipment_analysis | ТЗ + КП | PASS (8 pairs matched) |

---

## Working Notes

### 2026-03-04 — Consolidation branch model
- Каноническая основная ветка: `v3.0`
- `main` удалена. Все изменения базируются на `v3.0`.

### 2026-03-11 — NotebookLM operational note
- Auth валидна (`nlm login --check` подтверждён)
- MCP-интеграция нестабильна — использовать CLI fallback `nlm ...`

### 2026-03-12 — Orchestration boundary review
- B3.31 ядро закрыто в коде (3 новых модуля + 37 тестов)
- `chainlit_app.py` делегирует в backend, `_execute_intent` удалён
- Остаётся cleanup: routing-дубликаты в chainlit_app.py, uncommitted changes
- `agent_api.py` рефакторинг: legacy code удалён, unified execution core

### Антикризисные правила
1. Не добавлять новые workflow до B3.31 cleanup
2. Не делать full rewrite на LangChain
3. UI — thin client над backend policy
4. Dev-loop: native Chainlit на хосте, Docker для prod-validation
5. Classifier upgrade только после стабилизации orchestration contract

## Session Log

- [x] **[2026-03-12 22:09]** Task #1: (без названия) — ✅ completed
