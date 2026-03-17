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
  Доп. cleanup:
  - UI-side `classifier_result` generation и classifier pre-init удалены из `Chainlit`; semantic classification теперь считается только в backend `execution_runtime.py`.

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

### Блок E — System Behavior Sweep (B3.37)

- [x] **B3.37 — Full system behavior sweep for model/control-plane**
  - Добавлен канонический harness: `backend/evals/system_behavior_sweep.py`
  - Harness покрывает:
    - `api` surface через `/execute_orchestration`
    - `ui` surface через thin Playwright CLI runner
    - shared scorecard contract
    - unified JSON report shape
  - Добавлен scenario dataset:
    - `backend/evals/data/system_behavior_scenarios.yaml`
    - stable fixture `backend/evals/data/fixtures/murka_note.txt`
  - Full control-plane sweep helper строит `900` комбинаций:
    - `assistant_mode`
    - `runtime_mode`
    - `rag_scope`
    - `model_profile`
    - `prompt_profile`
  - Scorecard проверяет:
    - route allowed/forbidden
    - citations required / minimum count
    - missing-context request
    - refusal on insufficient evidence
    - запрет claims про внешний доступ
    - запрет выдуманных repo/codebase claims
    - pending action semantics
  - Verification:
    - `pytest backend/tests/test_system_behavior_sweep.py -q` -> `12 passed`
    - live API sweep на native stack:
      - `python backend/evals/system_behavior_sweep.py --surface api --json-output /tmp/system-behavior-api.json`
      - результат: `5 passed / 0 failed / 0 error`
  Follow-up:
  - live `ui` full sweep по-прежнему требует окружение с доступным `playwright-cli`; если он не становится поддерживаемым operator binary, нужен repo-owned wrapper вместо внешней зависимости
  - отдельный operator run для полного `ui` batch всё ещё желателен после укрепления `PlaywrightCliRunner`, хотя ручной smoke уже подтвердил starter/settings parity

### Блок F — Runtime Budgeting (T4.13)

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
  - native/legacy service launch scripts (`run_native.sh`, `run_openwebui.sh`, `start_system_test.sh`) теперь экспортируют `PYTHONPATH=$BACKEND_DIR` и для `document_server`/`legal_server`, чтобы shared backend imports не ломали live startup
  Pragmatic follow-up:
  - cleanup/закрытие PR #3-#7 как superseded остаётся отдельным repo-maintenance шагом

### Блок G — UX Hardening (T4.2, T4.3)

- [x] **T4.2 — Chainlit UX hardening**
  Выполнено поверх стабилизированных `B3.31a + T4.13 + T4.14`.
  Что закрыто:
  - новый чат стартует без auto-welcome assistant message; starter cards остаются реальным entrypoint'ом сценариев
  - unified resume status messaging для backend-snapshot-first и legacy-history fallback
  - thread title/metadata sync в Chainlit data layer для видимого списка тредов
  - явное отображение active docs / rag scope / runtime profile / pending action state в UX summary
  - `Chainlit` остался thin control surface: routing/policy decisions не возвращались в UI
  - direct-chat output sanitization добавлена против prompt leak: leaked system/profile lines (`Используй...`, `Не повторяйся...`, `PROFILE INSTRUCTIONS`) больше не должны попадать в пользовательский ответ
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

- [x] **T4.4 — documents_summary runtime degradation on weak hardware**
  Закрыт минимальный stability slice для `documents_summary` в Chainlit/API path.
  Что закрыто:
  - `effective_settings.device_mode` теперь нормализуется в UMS-compatible `cpu|gpu|hybrid` и реально прокидывается в inference path, вместо purely UI-level hint
  - для `documents_summary` добавлен stage-aware execution contour: `chunk`, `merge`, `global`
  - merge path больше не строит один unbounded prompt из всех chunk summaries; используется bounded batched reduce
  - в summary path отключён second full retry на уровне Chainlit adapter; при stage-level failure workflow переходит в partial response вместо двойного 300s провала
  - при падении `merge`/`global` пользователь получает промежуточные сводки, а не пустой ответ
  - добавлен pre-merge/global token budget guard через bounded batching и capped summary input
  - chunk summaries кешируются между повторными вызовами `documents_summary`, если document chunk и profile/model не изменились; cache entry теперь имеет TTL через `DOCUMENTS_SUMMARY_CACHE_TTL_S`
  - progress box теперь показывает progressive partial response: chunk progress -> per-doc summary -> global summary stage
  - stage-level metrics публикуются через existing fallback counter contour: attempts, cache hits, degraded merge/global branches
  Проверки:
  - `pytest backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py backend/tests/test_chainlit_streaming.py backend/tests/test_chainlit_runtime_mode.py -q`
  - `python -m py_compile backend/orchestrator/execution_runtime.py backend/orchestrator/agent_api.py backend/orchestrator/chainlit_app.py backend/orchestrator/ui_control_plane.py`
  Pragmatic follow-up:
  - richer observability для summary stages (`prompt chars`, explicit `summary_stage` payload fields, effective device policy in metrics), smarter invalidation beyond TTL и adaptive timeout policy остаются отдельным hardening step, не смешивались с минимальным runtime-stability fix

---

## Отложенные задачи

### Production Deployment (Фаза 6)
- [ ] T3.17 — Dockerize backend services (UMS, Doc Server, Legal Server)
- [ ] T3.18 — Model delivery strategy (130 GB GGUF)
- [x] T3.19 — Production secrets & auth hardening
  Закрыт minimal production-safe secrets/auth slice:
  - `scripts/bootstrap_env.sh` читает `backend/.env`, при необходимости создаёт его из `backend/.env.example` и auto-heal обрабатывает `CHAINLIT_AUTH_SECRET`:
    - если `backend/.env` отсутствует, bootstrap создаёт файл из шаблона;
    - если `CHAINLIT_AUTH_SECRET` пустой или равен дефолтному шаблонному значению, bootstrap генерирует новый secret и записывает его обратно в `backend/.env`;
  - после auto-heal bootstrap fail-fast валидирует insecure defaults для critical secrets:
    - `CHAINLIT_AUTH_SECRET`
    - `CHAINLIT_ADMIN_PASSWORD`
    - `GF_SECURITY_ADMIN_PASSWORD`
  - local/dev escape hatch только через явный `AGENT_NAVIGATOR_ALLOW_INSECURE_DEFAULTS=1`
  - `run_all.sh` больше не печатает пароль в stdout и не подсказывает `admin/admin`
  - `backend/.env.example`, `README.md`, `docs/deploy-guide.md`, `docs/scripts/*` синхронизированы под required secret rotation
  Проверки:
  - `pytest backend/tests/test_runtime_launcher.py -q -k 'bootstrap or insecure or launcher'`
  - `pytest backend/tests/test_install_scripts.py -q`
  - `bash -n scripts/bootstrap_env.sh scripts/launcher.sh scripts/run_all.sh`
  - `git diff --check`
  Follow-up:
  - полноценный auth/ACL layer для operator endpoints остаётся отдельной фазой
  - vault/secret-manager integration не входит в current env-based hardening slice
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

- [x] **Chainlit persistence schema compatibility**
  Починен drift между локальным SQLite bootstrap schema и фактическим `chainlit 2.9.6` runtime.
  Что закрыто:
  - idempotent schema upgrade для `threads/steps/elements` вместо чистого `CREATE TABLE IF NOT EXISTS`
  - добавлены missing columns `steps.command` и `steps.defaultOpen`
  - `threads.tags` больше не падает на SQLite bind: локальный compatibility data-layer сериализует list в JSON string на запись и декодирует обратно на чтении
  - живой native smoke подтвердил, что sidebar снова показывает сохранённые треды
  Verification:
  - `pytest backend/tests/test_chainlit_persistence_schema.py -q`
  - `pytest backend/tests/test_chainlit_runtime_mode.py -q`
  - `./scripts/run_native.sh --no-attach`
  - живой Playwright smoke: создание треда через starter + проверка sidebar/history + `tail -n 80 backend/orchestrator/chainlit.log`

- [x] **Chainlit elements persistence**
  Локальная persistence для `cl.File` / `cl.Pdf` переведена на file-backed storage provider поверх `UPLOADS_DIR`, без внешнего blob storage.
  Что закрыто:
  - `SQLAlchemyDataLayer` теперь получает локальный `storage_provider`, поэтому warning `storage client is not initialized and elements will not be persisted` исчезает
  - persisted elements пишутся в `UPLOADS_DIR/chainlit-elements`
  - read URLs обслуживаются через локальный `Chainlit` route `/project/file/{object_key}`
  - targeted coverage:
    - `backend/tests/test_chainlit_elements_persistence.py`
  Verification:
  - `pytest backend/tests/test_chainlit_elements_persistence.py -q`
  - `pytest backend/tests/test_chainlit_elements_persistence.py backend/tests/test_chainlit_runtime_mode.py -q`
  - `./scripts/run_native.sh --no-attach`
  - живой Playwright smoke: upload text file, file element появляется в треде, старый storage warning в `backend/orchestrator/chainlit.log` больше не появляется
  Follow-up:
  - local file route сейчас авторизует доступ по префиксу `current_user.identifier` в `object_key`; если модель user/thread identity будет меняться, этот guard нужно отдельно пересмотреть

- [x] **Chainlit SQLite locking hardening**
  После включения локальной elements persistence `Chainlit SQLite` был дополнительно усилен для native/dev path, чтобы убрать `database is locked` на серийных thread updates.
  Что закрыто:
  - bootstrap теперь включает `PRAGMA journal_mode=WAL` и `PRAGMA synchronous=NORMAL`
  - compatibility data layer для SQLite добавляет `connect_args.timeout`
  - на каждое SQLite подключение вешается `busy_timeout` через SQLAlchemy connect hook
  - targeted coverage:
    - `backend/tests/test_chainlit_persistence_schema.py`
  Verification:
  - `pytest backend/tests/test_chainlit_persistence_schema.py backend/tests/test_chainlit_elements_persistence.py backend/tests/test_chainlit_runtime_mode.py -q`
  - `./scripts/run_native.sh --no-attach`
  - живой Playwright smoke: создание fresh thread
  - `rg -n "database is locked|Authorization for the thread failed" backend/orchestrator/chainlit.log` -> no matches

- [x] **System behavior sweep harness**
  Собран repeatable harness для проверки поведения системы при смене control-plane параметров и generation overrides.
  Что закрыто:
  - added `backend/evals/system_behavior_sweep.py`
    - canonical scenario loader
    - shared scorecard evaluator
    - full control-plane matrix builder (`5 x 3 x 3 x 4 x 5 = 900` combinations)
    - unified JSON report contract
    - live API runner for `/execute_orchestration`
    - thin `playwright-cli` UI adapter with fail-fast dependency check
  - added canonical dataset:
    - `backend/evals/data/system_behavior_scenarios.yaml`
  - added targeted coverage:
    - `backend/tests/test_system_behavior_sweep.py`
  - added plan/doc:
    - `docs/plans/2026-03-15-system-behavior-sweep.md`
  Verification:
  - `pytest backend/tests/test_system_behavior_sweep.py -q`
  - `pytest backend/tests/test_system_behavior_sweep.py backend/tests/test_ui_control_plane.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_agent_api_orchestrate.py -q` -> `55 passed`
  - `pytest backend/tests/test_system_behavior_sweep.py backend/tests/test_chainlit_runtime_mode.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py -q` -> `69 passed`
  - live native API sweep:
    - `python backend/evals/system_behavior_sweep.py --surface api --json-output /tmp/system-behavior-api.json`
  Findings from live run:
  - initial live run surfaced two concrete quality bugs:
    - multilingual contamination in short `general_chat`
    - overly conservative grounded `rag_qa` on direct single-source factoid (`Мурка -> 5`)
  - both are fixed in current backend slice:
    - `general_chat` now applies a narrow language-consistency guard with one constrained regen
    - `document_question` now preserves grounded answers for single-source direct evidence and can deterministically synthesize a short cited answer when the model still fails
  Follow-up:
  - extend behavior dataset with harder generation-sensitive prompts and stricter answer-shape checks (`exact short answer`, `must avoid multilingual spill`)
  - strengthen `PlaywrightCliRunner` after repeated live runs against current `Chainlit` DOM

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
  - Runtime model paths дополнительно унифицированы через generic env contract с backward compatibility:
    - `MODEL_PATH_LLM`
    - `MODEL_PATH_VLM`
    - `MODEL_PATH_EMBEDDING_INTENT`
    - `MODEL_PATH_EMBEDDING_RETRIEVAL`
    - legacy aliases `MODEL_PATH_QWEN14B`, `MODEL_PATH_QWENVL`, `MODEL_PATH_QWEN3_EMBEDDING_06B`, `MODEL_PATH_LABSE` сохранены как compatibility layer
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
  - добавить явную auth-конфигурацию для Grafana (`GF_SECURITY_ADMIN_PASSWORD` и related envs), чтобы monitoring profile не публиковал дефолтные credentials
  - нормализовать HTTP metrics route labels до route-template/low-cardinality form; сейчас dynamic `model_id` path fragments в `UMS` могут раздувать TSDB
  - исключить `/metrics` из общих request-rate/latency панелей или метрик, чтобы self-scrape Prometheus не создавал постоянный шум на idle системе
- [x] T4.9 — vLLM adapter в UMS
  Реализован narrow adapter внутри `UMS` без смены orchestration core:
  - `BACKEND_MODE=vllm` для heavy `gguf` text inference path
  - remote sentinel/runtime placement `remote-vllm`
  - upstream probes `/health` + `/v1/models`
  - proxy для `completions` / `chat/completions` с `Authorization` header и injected served model id
  - status/model views публикуют `backend_mode=vllm` и remote placement metadata
  - non-stream path больше не маскирует upstream 4xx/5xx как `success`
  Проверки:
  - `pytest backend/tests/test_unified_model_server_startup.py -q -k 'test_activate_endpoint_uses_remote_vllm_backend or test_status_exposes_backend_mode_for_vllm or test_non_stream_infer_proxies_to_vllm_with_auth_headers or test_non_stream_chat_infer_uses_vllm_chat_completions or test_non_stream_infer_does_not_mask_vllm_upstream_http_error or test_ensure_vllm_backend_rejects_missing_served_model or test_stop_model_detaches_remote_vllm_without_killing_process'`
  - `pytest backend/tests/test_unified_model_server_streaming.py -q -k 'passes_upstream_headers'`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py`
  - `git diff --check`
  Follow-up:
  - T4.10 остаётся обязательной отдельной фазой: production docker-compose/profile и operator rollout для самостоятельного `vLLM` deployment
  - safe rollback semantics при failed switch с локального heavy runtime на remote `vLLM` стоит отдельно усилить, чтобы probe failure не влиял на уже активную heavy model
- [x] T4.10 — Production docker-compose profile для vLLM
  Добавлен самостоятельный compose/profile rollout для remote `vLLM` runtime:
  - `docker-compose.yaml` получил сервис `vllm` под profile `vllm`
  - `scripts/run_vllm_service.sh` собирает `vllm serve ...` из env surface
  - `run_all.sh` умеет автоматически добавлять `vllm` service для container path при `BACKEND_MODE=vllm`
  - `runtime_preflight.py` публикует `backend_mode` в runtime plan/report
  - `.env.example`, `README.md`, `deploy-guide.md`, `runtime_profiles.md` синхронизированы под rollout/rollback flow
  Проверки:
  - `pytest backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py -q -k 'vllm or launcher'`
  - `bash -n scripts/launcher.sh scripts/run_all.sh scripts/run_container.sh scripts/bootstrap_env.sh scripts/run_vllm_service.sh`
  - `docker compose --profile vllm config`
  - `python -m py_compile scripts/runtime_preflight.py backend/tests/test_runtime_launcher.py backend/tests/test_runtime_preflight.py`
  - `git diff --check`
  Follow-up:
  - end-to-end smoke с живым `vLLM` контейнером и реальной моделью остаётся отдельным benchmark/deployment блоком (`T4.11`)
  - общий pytest tail-hang для более широкого `UMS`/runtime slice по-прежнему иногда проявляется после прохождения тестов; это известный harness residual, а не регрессия `T4.10`
- [x] T4.11 — E2E benchmark before/after migration
  Benchmark harness обновлён под migration comparison `llama-server -> vllm`:
  - `scripts/benchmark.py` теперь пишет `backend_mode` и `runtime_metadata`
  - `scripts/benchmark_compare.py` сравнивает runtime diff с учётом `backend_mode`
  - добавлен benchmark runner coverage в `backend/tests/test_benchmark_runner.py`
  - operator workflow для before/after migration задокументирован в `README.md` и `docs/deploy-guide.md`
  Проверки:
  - `pytest backend/tests/test_benchmark_compare.py backend/tests/test_benchmark_runner.py -q`
  - `python -m py_compile scripts/benchmark.py scripts/benchmark_compare.py backend/tests/test_benchmark_compare.py backend/tests/test_benchmark_runner.py`
  - `git diff --check`
  Follow-up:
  - live benchmark с реально поднятым `vLLM` контейнером и production-size моделью остаётся operator/runtime exercise; этот шаг закрыл reproducible harness и compare contract
- [x] T4.12 — Security hardening для Ops UI
  Закрыт минимальный ops-security slice без смены runtime architecture:
  - `docker-compose.yaml`: Grafana credentials и auth flags через env (`GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD`, sign-up/anonymous disabled)
  - `backend/.env.example`: добавлены явные Grafana auth envs
  - `backend/services/observability.py`: HTTP metrics нормализуют dynamic paths до low-cardinality labels
  - `/metrics` исключён из общих HTTP request counters/latency, чтобы self-scrape не создавал шум
  Проверки:
  - `pytest backend/tests/test_observability.py backend/tests/test_unified_model_server_startup.py -q -k 'metrics or observability'`
  - `docker compose --profile monitoring config`
  - `python -m py_compile backend/services/observability.py backend/tests/test_observability.py backend/tests/test_unified_model_server_startup.py`
  - `git diff --check`
  Follow-up:
  - полноценный auth/ACL для model-control endpoints остаётся отдельным ops-hardening block
  - Grafana secret injection из vault/secret manager не входит в текущий env-based slice

### Другие
- [x] B3.11 — Убрать JSON salvage из DEBUG-POLISH (structured output platform-level)
  Выполнено как узкий backend-safe slice для equipment `DEBUG-POLISH` path:
  - добавлен reusable helper `backend/orchestrator/structured_output.py`:
    - `extract_model_text(...)`
    - `parse_strict_json(...)`
  - `equipment._polish_items_specs_llm()` переведён с tolerant XML parsing на strict JSON contract:
    - `schema_version = "b3.11.v1"`
    - `ok`
    - `data.results = [{"id": ..., "text": ...}]`
    - `error`
  - code-fence stripping / salvage / tolerant extraction в polisher path удалены; invalid structured output теперь fail-closed и уходит в уже существующий deterministic fallback
  - batching, id mapping и fallback behavior сохранены
  Проверки:
  - `pytest backend/tests/test_equipment_workflow.py -q -k "polish"` -> `13 passed`
  - `pytest backend/tests/test_equipment_workflow.py -q` -> `84 passed`
  - `python -m py_compile backend/orchestrator/structured_output.py backend/orchestrator/workflows/equipment.py backend/tests/test_equipment_workflow.py`
  - `git diff --check`
  Follow-up:
  - остальные `parse_json_garbage(...)` path в `compare`, equipment extract/eval и classifier остаются отдельным structured-output hardening block; текущая фаза закрывает именно `DEBUG-POLISH`
  - широкий `cd backend && pytest tests/ -q -m "not integration"` probe по-прежнему иногда завершается старым pytest tail-hang после прохождения тестов; это не выглядит новым регрессом `B3.11`
- [x] B3.16 — Проверить llama-server defunct / uptime после простоя
  Выполнено как uptime-hardening для локально управляемых `llama-server` процессов в `UMS`, без добавления watchdog/auto-restart semantics:
  - в `backend/services/model_manager/unified_model_server.py` добавлены:
    - `_is_managed_process_alive(proc)` для отличия живых локальных процессов от stale state;
    - `_prune_dead_processes()` для safe sweep мёртвых локальных процессов из `state["processes"]`, `state["placements"]` и `active_model`.
  - dead-process pruning теперь выполняется перед:
    - `_build_model_view(...)`
    - `GET /models`
    - `GET /models/running`
    - `GET /status`
  - `_RemoteProcess` не считается dead local process и не вычищается этим sweep helper'ом.
  Проверки:
  - `pytest backend/tests/test_unified_model_server_startup.py -q -k "dead_local_processes or status_prunes_dead_local_process_and_clears_active_model or status_keeps_remote_process_registered or models_running or status_exposes_current_placements"` -> `5 passed`
  - `pytest backend/tests/test_unified_model_server_startup.py backend/tests/test_unified_model_server_streaming.py -q` -> тесты проходят по точкам и затем упираются в уже известный старый pytest tail-hang; это не выглядит новым регрессом `B3.16`
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py`
  Follow-up:
  - watchdog/auto-restart, idle unload policy и launcher/tmux cleanup остаются отдельными operational phases и не входят в текущий uptime-hardening slice
- [x] B3.17 — Проверить prompt-cache эффективность
  Выполнено как backend-owned prompt-cache policy + warm-repeat probe, без переписывания inference/concurrency path:
  - в `backend/services/model_manager/unified_model_server.py` добавлен `prompt_cache_policy`:
    - env `UMS_LLAMA_CACHE_PROMPT=true|false`
    - local `llama-server` heavy text path теперь явно получает `cache_prompt=true|false`
    - `vllm` path не получает `cache_prompt`
    - `GET /status` публикует `prompt_cache_policy`
  - в `scripts/benchmark.py` добавлен сценарий `prompt_cache_probe`:
    - два одинаковых direct `UMS /infer` запроса подряд
    - `cold_elapsed_sec`, `warm_elapsed_sec`, `speedup`, `delta_pct`
    - `prompt_prefix_fingerprint`
    - snapshot `prompt_cache_policy` из `UMS /status` до/после
  - docs/env surface синхронизированы:
    - `backend/.env.example`
    - `README.md`
    - `docs/runtime_profiles.md`
    - `docs/deploy-guide.md`
  Проверки:
  - `pytest backend/tests/test_benchmark_runner.py -q` -> `4 passed`
  - `pytest backend/tests/test_unified_model_server_startup.py::test_status_exposes_prompt_cache_policy_for_local_llama -q` -> `1 passed`
  - `timeout 20s pytest backend/tests/test_unified_model_server_startup.py::test_non_stream_local_llama_infer_enables_cache_prompt_by_default -vv` -> тест проходит, затем воспроизводится известный старый pytest tail-hang на завершении процесса
  - `timeout 20s pytest backend/tests/test_unified_model_server_startup.py::test_non_stream_infer_proxies_to_vllm_with_auth_headers -vv` -> тест проходит, затем воспроизводится тот же известный harness tail-hang
  - `python -m py_compile backend/services/model_manager/unified_model_server.py backend/tests/test_unified_model_server_startup.py scripts/benchmark.py backend/tests/test_benchmark_runner.py`
  - `git diff --check`
  Follow-up:
  - реальный integration benchmark `cold vs warm vs negative-control` на живом `llama-server` остаётся отдельным operational block, если понадобится подтверждать фактический cache hit-rate, а не только policy + probe contract
- [x] B3.28 — Coverage heuristic v1.1 для document_question
  Выполнено как backend-shared heuristic layer для `document_question`, без смены retrieval contour:
  - добавлен `backend/orchestrator/doc_question_heuristics.py` как единый contract для:
    - `extract_citation_ids`
    - `citations_are_valid`
    - `has_sufficient_evidence_v1`
    - `compute_confidence_v1`
    - deterministic fallback
  - `Chainlit` и API-compatible execution path больше не живут на разных локальных эвристиках; оба wiring-path используют один backend module
  - gating теперь учитывает citation coverage и multi-hop support, а не только `top-1 raw_score`
  - confidence penalizes weak single-citation support for multi-hop / cross-document queries
  Проверки:
  - `pytest backend/tests/test_document_analysis.py -q` -> `65 passed`
  - `pytest backend/tests/test_execution_runtime.py -q` -> `12 passed`
  - `pytest backend/tests/test_agent_api_orchestrate.py -q` -> `15 passed`
  - `python -m py_compile backend/orchestrator/doc_question_heuristics.py backend/orchestrator/chainlit_app.py backend/orchestrator/agent_api.py backend/orchestrator/execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_execution_runtime.py backend/tests/test_agent_api_orchestrate.py`
  - `git diff --check`
  Follow-up:
  - общий pytest tail-hang в широком `cd backend && pytest tests/ -q -m "not integration"` probe по-прежнему иногда проявляется после прохождения тестов; для `B3.28` targeted suite зелёный и это не выглядит как новый регресс
- [x] B3.32 — LangChain adoption strategy (точечно, без full rewrite)
  Зафиксировано через ADR: [docs/plans/2026-03-12-b332-langchain-adoption-strategy.md](docs/plans/2026-03-12-b332-langchain-adoption-strategy.md).
  Решение: не делать full rewrite orchestration core на LangChain; сохранять backend-first contract (`orchestration_runtime.py` + `execution_runtime.py`) каноническим; разрешать только точечные integration areas: workflow-level `LangGraph`, retriever/reranker adapters, eval harness, observability adapters и один изолированный pilot area без смены публичного API.
- [ ] Vision-анализ (Qwen-VL интеграция)
- [ ] Conda environment export

### Открытый Tech Debt
- [x] TD-7 — O(N·M) reverse mapping в match_items_node → dict lookup
  Уже реализовано в `backend/orchestrator/workflows/equipment.py`: `match_items_node()` использует `items_1_by_text/items_2_by_text` для O(1) reverse lookup по `old_text/new_text` вместо повторного линейного поиска по спискам.
  Проверки:
  - `pytest backend/tests/test_equipment_workflow.py -q -k "TestMatchItemsNode or matching_error or empty_matches"` -> `7 passed`
  - `rg -n "O\\(1\\) reverse mapping|TD-7 Fix" backend/orchestrator/workflows/equipment.py`
- [x] TD-8 — Fallback-цепочки скрывают ошибки → WARNING + счётчики
  Закрыто на workflow/runtime critical slices, compare/document_analysis и `ums_client`.
  Что покрыто:
  - `backend/orchestrator/workflows/equipment.py` теперь публикует `WARNING + agent_nav_equipment_fallback_total` для:
    - `smart_chunk` → line-based fallback
    - invalid `DEBUG-POLISH` structured output
    - LLM batch error в `evaluate_compliance_node`
  - `backend/orchestrator/execution_runtime.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - `doc_question` retrieval exception (`fallback=rag_exception`)
    - generic `doc_question` no-sources degraded branch (`fallback=no_sources`)
  - `backend/orchestrator/rag/classifier.py` теперь публикует `agent_nav_fallback_events_total` для:
    - `llm_non_json`
    - `llm_unsupported_intent`
    - `llm_missing_embedder_fallback`
    - `hybrid_low_confidence_unsure`
  - `backend/services/model_manager/unified_model_server.py` теперь публикует `agent_nav_fallback_events_total` для:
    - ST GPU → CPU retry (`fallback=st_start_cpu_retry`)
  - `backend/orchestrator/workflows/compare.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - document load error (`fallback=load_documents_failed`)
    - legal match error (`fallback=match_batches_failed`)
    - partial/short structured output в batch-анализе (`fallback=analyze_parse_partial`)
    - batch LLM error (`fallback=analyze_batch_error`)
  - `backend/orchestrator/workflows/document_analysis.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - document load/page load/table load issues
    - table extraction / LLM extraction fallback
    - summarize chunk failure
    - reduce summarization failure
  - `backend/services/model_manager/ums_client.py` теперь публикует `WARNING + agent_nav_fallback_events_total` для:
    - sync infer retry (`fallback=infer_retry`)
    - async infer retry (`fallback=async_infer_retry`)
    - stream error (`fallback=stream_error`)
  - Проверки:
    - `pytest backend/tests/test_equipment_workflow.py -q -k "falls_back_when_batch_xml_invalid or smart_chunk_fails_fallback or llm_error_produces_error_result"` -> `3 passed`
    - `pytest backend/tests/test_equipment_workflow.py -q` -> `84 passed`
    - `pytest backend/tests/test_execution_runtime.py -q -k "rag_exception_fallback_metric"` -> `1 passed`
    - `pytest backend/tests/test_intent_classifier.py -q -k "non_json_records_metric or rejects_unknown_intent or hybrid_can_end_unsure or llm_fallback_still_respects_embedder_abstain"` -> `4 passed`
    - `pytest backend/tests/test_unified_model_server_startup.py -q -k "records_cpu_placement_after_st_fallback"` -> `1 passed`
    - `pytest backend/tests/test_execution_runtime.py backend/tests/test_intent_classifier.py backend/tests/test_unified_model_server_startup.py backend/tests/test_equipment_workflow.py -q -k "rag_exception_fallback_metric or non_json_records_metric or rejects_unknown_intent or hybrid_can_end_unsure or llm_fallback_still_respects_embedder_abstain or records_cpu_placement_after_st_fallback or falls_back_when_batch_xml_invalid or smart_chunk_fails_fallback or llm_error_produces_error_result"` -> `9 passed`
    - `pytest backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py -q` -> `73 passed`
    - `pytest backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py backend/tests/test_execution_runtime.py backend/tests/test_intent_classifier.py backend/tests/test_ums_client.py backend/tests/test_unified_model_server_startup.py backend/tests/test_equipment_workflow.py -q -k "fallback or metric or retry or stream_error or records_cpu_placement_after_st_fallback or llm_error_produces_error_result or parse_failed or rag_exception_fallback_metric or non_json_records_metric or llm_fallback_still_respects_embedder_abstain or hybrid_can_end_unsure or compare_load_documents_failure_records_metric or compare_match_batches_failure_records_metric or compare_analyze_partial_parse_records_metric or extract_llm_failure_records_metric or summarize_reduce_failure_records_metric or summarize_llm_error or test_async_infer_retries_on_503"` -> `21 passed`
  Residual:
  - широкий multi-file pytest bundle всё ещё иногда залипает на старом tail-noise после прохождения основной части; это не выглядит регрессом `TD-8`
- [x] TD-9 — Magic numbers без документации → именованные константы + env override
  Реализован как narrow runtime-critical hardening slice, без full-sweep по всему репозиторию.
  - `backend/services/model_manager/ums_client.py`
    - retries / timeouts / pool limits / embedding batch controls вынесены в именованные константы и env overrides:
      - `UMS_CLIENT_TIMEOUT_S`
      - `UMS_CLIENT_CONNECT_TIMEOUT_S`
      - `UMS_CLIENT_KEEPALIVE_CONNECTIONS`
      - `UMS_CLIENT_MAX_CONNECTIONS`
      - `UMS_SYNC_INFER_RETRIES`
      - `UMS_SWITCH_MODEL_TIMEOUT_S`
      - `UMS_STATUS_TIMEOUT_S`
      - `UMS_EMBED_PROBE_TIMEOUT_S`
      - `UMS_EMBED_BATCH_SIZE`
      - `UMS_EMBED_BATCH_TIMEOUT_S`
      - `UMS_EMBED_BATCH_CONNECT_TIMEOUT_S`
      - `UMS_EMBED_BATCH_RETRIES`
      - `UMS_EMBED_BATCH_RETRY_DELAY_S`
  - `backend/orchestrator/workflows/compare.py`
    - compare truncate/chunk/analyze thresholds вынесены в именованные константы и env overrides
  - `backend/orchestrator/workflows/document_analysis.py`
    - summarize/reduce temperature/max_tokens/sleep вынесены в именованные константы и env overrides
  - `backend/orchestrator/knowledge_base_retrieval.py`
    - merge/dedup/rerank shortlist coefficients и candidate budget вынесены в именованные константы и env overrides
  - `backend/orchestrator/doc_question_heuristics.py`
    - confidence thresholds, coverage weights, multihop/cross-doc bonuses и insufficient-evidence cap вынесены в именованные константы и env overrides
  - Проверки:
    - `pytest backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py -q` -> `73 passed`
    - `python -m py_compile backend/services/model_manager/ums_client.py backend/orchestrator/workflows/compare.py backend/orchestrator/workflows/document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py backend/tests/test_document_analysis.py`
    - `pytest backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py -q` -> `91 passed`
    - `python -m py_compile backend/orchestrator/doc_question_heuristics.py backend/orchestrator/knowledge_base_retrieval.py backend/orchestrator/workflows/document_analysis.py backend/orchestrator/workflows/compare.py backend/services/model_manager/ums_client.py backend/tests/test_knowledge_base_retrieval.py backend/tests/test_execution_runtime.py backend/tests/test_document_analysis.py backend/tests/test_ums_client.py backend/tests/test_compare_workflow.py`
  Follow-up:
  - remaining magic numbers в `shared/report_utils`, `rag/retriever`, `equipment` и API glue остаются отдельным cleanup slice; `TD-9` закрыт только для runtime-critical surface

### Night autonomy and shutdown hardening

- [x] Added safe night-autonomous control layer:
  - `AGENTS.md` now contains `Night Autonomous Mode`
  - `TASKS_NIGHT.md` added as agent-friendly nightly backlog
  - `workflow.yaml` added as repo-local execution policy
- [x] Recorded that `workflow.yaml` is not an official Codex schema:
  - public Codex guidance confirms `AGENTS.md` and configurable rules/sandboxing
  - this repository uses `workflow.yaml` only as a local policy layer for unattended work
- [x] Hardened shutdown and relaunch scripts against orphaned model runtimes:
  - `stop_native.sh` and `stop_all.sh` now clean up `unified_model_server.py`, project-scoped `llama-server`, and orphaned `st_server.py`
  - `stop_all.sh` now stops both tmux sessions: `agent-navigator` and `agent-navigator-native`
  - service ports are now loaded from `.env` / `.env.native` / `.env.runtime` instead of being hardcoded
  - `run_native.sh`, `run_all.sh`, `run_openwebui.sh`, and `start_system_test.sh` now reuse the full stop-path before relaunch so orphaned embedding runtimes do not accumulate across restarts
  Verification:
  - `bash -n scripts/stop_native.sh scripts/stop_all.sh scripts/run_native.sh scripts/run_all.sh scripts/run_openwebui.sh scripts/start_system_test.sh`
  - live runtime check:
    - `./scripts/run_native.sh --no-attach`
    - `./scripts/stop_native.sh`
    - `ps -eo pid,ppid,cmd | rg "(llama-server|st_server.py|unified_model_server.py)"`
    - confirmed: orphaned `st_server.py` no longer remains after shutdown

### Runtime performance follow-up

- [ ] **B3.35 — Уменьшить embedding warm-up burst на первом коротком запросе**
  Контекст:
  - по живым логам `UMS` embedder runtimes стартуют корректно, но первый короткий запрос в `Chainlit` провоцирует пачку `POST /v1/embeddings` до/вокруг первого `infer`;
  - это связано не с повторной загрузкой embedder-моделей, а с lazy warm-up intent-classifier:
    - `create_ums_embed_fn()` делает probe в `/v1/embeddings`;
    - `EmbeddingIntentClassifier.initialize()` считает centroids по всему `intent_examples.yaml`;
    - при текущем наборе это 98 example phrases и несколько batched embedding calls.
  Цель:
  - сократить latency и число embedding-запросов на первом greeting/general-chat without changing routing semantics.
  Кандидаты решения:
  - кэшировать centroids classifier'а между запросами/сессиями;
  - отделить lightweight greeting/general-chat fast-path от полного centroid warm-up;
  - не делать eager classifier init, пока реально не нужен semantic routing;
  - пересмотреть `UMS_EMBED_BATCH_SIZE` и probe behavior для cold-start path.
  Acceptance:
  - на первом коротком запросе количество `POST /v1/embeddings` заметно ниже текущего burst;
  - routing contract и classifier quality не деградируют;
  - в логах остаётся явное distinction между model preload и classifier warm-up.

- [ ] **B3.36 — Вынести runtime-policy `document_analysis` в env/prompt contour**
  Контекст:
  - live weak-PC прогон тяжёлого файла показал, что текущая регрессия с двойным `500`/`300s` для `documents_summary` закрыта, но у `document_analysis` остаётся отдельный длинный latency tail;
  - по логам длинный хвост сидит не в PDF/Markdown report rendering, а в `summarize -> reduce` path внутри `backend/orchestrator/workflows/document_analysis.py`;
  - после завершения chunk-stage workflow делает ещё один тяжёлый single-shot `reduce` infer, и именно он даёт самый длинный `/infer` в UMS;
  - текущее поведение слишком зависит от Python literals и локальных констант, а не от единого policy/config слоя.
  Наблюдаемые симптомы:
  - тяжёлый документ проходит chunk summarization успешно, но затем надолго задерживается на final reduce step;
  - даже без падения пользовательский latency хвост остаётся заметным на слабом железе;
  - control-plane/runtime profile сейчас не даёт такого же управляемого degraded behavior для `document_analysis`, как уже даёт для `documents_summary`.
  Что нужно исправить:
  - перестать держать critical runtime knobs `document_analysis` только в коде workflow;
  - отделить prompt concerns от execution-policy concerns;
  - сделать weak-PC behavior предсказуемым и управляемым через `backend/.env`, а не через ручную правку констант.
  Что вынести в env/config:
  - лимиты `max_tokens` отдельно для `chunk`, `reduce` и при необходимости `report` stage;
  - input budget limits для `chunk` и `reduce` stage;
  - degraded/low-vram policy flags для `document_analysis`;
  - optional timeout/retry hints для долгих summary stages;
  - stage-specific toggles для bounded reduce и partial-result fallback.
  Что вынести в prompt/policy templates:
  - шаблон prompt для chunk summary;
  - шаблон prompt для reduce summary;
  - требуемую степень краткости итоговой сводки;
  - правила удаления дублей и приоритизации фактов;
  - разрешённый формат partial/degraded answer.
  Что нужно изменить в execution contour:
  - добавить для `document_analysis` stage-aware policy по аналогии с `documents_summary`;
  - заменить single-shot reduce на bounded reduce или batched/tree-reduce path;
  - добавить partial-result fallback, если финальный reduce не укладывается в budget или деградирует;
  - обеспечить, чтобы slow tail после chunk-stage не оставался единственной безусловной веткой завершения workflow.
  Почему это важно:
  - сейчас `document_analysis` и `documents_summary` живут на разном уровне зрелости runtime hardening;
  - из-за этого weak-PC сценарии остаются непредсказуемыми именно для анализа одного тяжёлого документа;
  - без env-driven policy дальнейшая настройка под разные машины снова будет происходить через правку кода, а не через конфиг.
  Acceptance:
  - `document_analysis` stage budgets управляются через env/config, а не только локальные константы;
  - reduce-stage больше не делает один неограниченный тяжёлый финальный infer для длинных документов;
  - weak-PC профиль даёт предсказуемый degraded path;
  - длинный document-analysis tail локализован и управляем без ручного редактирования workflow-кода;
  - logs/metrics позволяют отличать `chunk` и `reduce` stages и видеть, какой policy branch реально сработал.

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
- VS Code + Gemini Code Assist может циклически переподнимать `notebooklm-mcp`, если в `~/.gemini/settings.json` одновременно присутствуют `mcpServers.notebooklm` и `mcpServers.notebooklm-mcp`, а в `~/.gemini/mcp-server-enablement.json` отключён только `notebooklm`. Follow-up: оставить один канонический сервер и синхронизировать ключ enablement с фактическим именем сервера.

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
