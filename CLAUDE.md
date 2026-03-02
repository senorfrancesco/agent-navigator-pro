# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Navigator Pro — микросервисная агентная система для анализа юридических документов и смет. Python 3.11, FastAPI, LangGraph, локальные LLM (Qwen) через llama-server. Ветка: `feature/v3.0-agentic-system`.

**Архитектура:**
- **Frontend:** Chainlit (Docker, порт 3000) — чат, загрузка файлов, `cl.Step` для визуализации workflow
- **Backend:** Python-микросервисы на хосте (порты 8000-8090)
- **Обмен файлами:** Bind-mount `backend/open_webui_uploads/` → `/app/uploads` в контейнере
- **Оркестрация:** LangGraph workflows для сравнения документов и анализа смет
- **RAG:** Адаптивный tiered pipeline (BM25 + Dense + RRF, без внешних LLM-вызовов на классификацию)
- **Hardware:** Автоматический профайлинг GPU/CPU → выбор tier → расчёт gpu_layers/ctx_size

**Open WebUI** — legacy, доступен через `docker compose --profile legacy up` на порту 3001.

## Development Commands

### Окружение

```bash
conda activate diploma_llm
# или
source activate_env.sh
```

### Запуск системы

```bash
./scripts/run_all.sh        # Полный запуск (tmux + Docker + health-checks + ожидание модели)
./scripts/stop_all.sh        # Остановка всех сервисов
./scripts/restart_all.sh     # Перезапуск
tmux attach-session -t agent-navigator  # Подключение к сессии
```

`run_all.sh` создаёт tmux-сессию `agent-navigator` с 6 окнами:
`chainlit` → `agent-api` → `doc-server` → `legal-server` → `ums` → `monitor`

Скрипт после запуска ожидает `/health` от каждого сервиса и загрузку Qwen LLM через `/status` UMS (до 3 минут), затем выводит итоговый статус.

Docker-образ Chainlit **не пересобирается** при каждом запуске. Для пересборки после изменения кода:
```bash
docker compose build chainlit && docker compose up -d chainlit
```

**Важно:** `chainlit_app.py` копируется в образ при сборке — bind mount только для `open_webui_uploads/`. Любое изменение кода требует `docker compose build chainlit`.

### Ручной запуск сервисов

```bash
cd backend/orchestrator && python agent_api.py                                    # порт 8000
cd backend/services/document_server && uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001
cd backend/services/legal_server && uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002
cd backend/services/model_manager && python unified_model_server.py               # порт 8090
```

### Тесты

```bash
./start_system_test.sh                        # Интеграционный тест всех сервисов
cd backend && pytest tests/                   # Unit-тесты
pytest tests/test_integration_full.py -v      # Конкретный тест
```

## Архитектура

### Два входных потока

**Chainlit (основной)** — `chainlit_app.py` работает внутри Docker, напрямую вызывает LangGraph workflows и `ums_client`. Не проксирует через agent_api.

**Agent API (legacy/совместимость)** — `agent_api.py` на хосте (порт 8000), OpenAI-compatible `/v1/chat/completions`. Используется Open WebUI и внешними клиентами.

### Request Flow (Chainlit)

1. User → Chainlit (Docker:3000) — чат, загрузка файлов
2. `_detect_intent()` → определение intent по ключевым словам и наличию файлов
3. Файлы → HTTP POST к Document Server → парсинг + чанкинг
4. Intent `compare_documents` / `equipment_analysis` → LangGraph workflow (каждый node обёрнут в `cl.Step`)
5. Intent `document_question` → `AdaptiveRAGPipeline` (или fallback на naive context stuffing)
6. Intent `general_chat` → `ums_client.async_infer_stream()` → SSE стриминг

### LangGraph Workflows

`backend/orchestrator/workflows/`:

**compare.py** — Сравнение документов:
`load_documents_node` → `batch_match_node` (LaBSE embeddings) → `deep_compare_node` (LLM) → `generate_report_node`

**equipment.py** — Анализ смет/ТЗ: аналогичная структура для технических спецификаций.

State — TypedDict, каждый node возвращает dict-обновления. Ошибки накапливаются в `state["errors"]`.

### Adaptive RAG Pipeline

`backend/orchestrator/rag/`:

| Tier | Режим | Стратегия |
|------|-------|-----------|
| 1 | `simple` | BM25+Dense → RRF → top-5 → Generate |
| 2 | `corrective` | EmbeddingIntentClassifier → Hybrid → Z-score grading → (pass/Rocchio expand) |
| 3 | `agentic` | Итеративная переформулировка (до 3 итераций, Rocchio) |
| 4 | `multi-agent` | Fallback на agentic (LangGraph-версия в backlog) |

- **`retriever.py`** — BM25 для русского текста (встроенные стоп-слова), Dense через LaBSE, RRF fusion (k=60), Z-score grading
- **`classifier.py`** — Centroid-based классификация intent через embeddings (без LLM)
- **`chunker.py`** — Section-aware чанкинг юридических документов
- **`pipeline.py`** — Оркестрация tier-выбора

### Микросервисы

| Сервис | Порт | Ключевые endpoints |
|--------|------|--------------------|
| Agent API | 8000 | `/v1/chat/completions`, `/v1/models`, `/health` |
| Document Server | 8001 | `/load_document`, `/chunk_text`, `/health` |
| Legal Server | 8002 | `/batch_match`, `/health` |
| UMS | 8090 | `/infer`, `/v1/embeddings`, `/status`, `/health` |

### Unified Model Server (UMS)

`backend/services/model_manager/unified_model_server.py` — управляет жизненным циклом моделей:

- **Предзагрузка:** Qwen-14B LLM запускается автоматически при старте UMS (lifespan)
- **Hardware profiling:** При старте определяет GPU/CPU → TierSelector → VRAMCalculator → вычисляет `gpu_layers` и `ctx_size`
- **Динамическое переключение:** Одна тяжёлая модель (GGUF) в памяти; при переключении старая выгружается
- **Модели:** `qwen-14b-llm` (порт 8091), `qwen-vl-8b` (порт 8092), `labse-embedding` (порт 8093)
- **LaBSE:** ONNX FP32 экспорт (1.2-1.4x быстрее SentenceTransformers на CPU)

Конфиг модели можно переопределить через `.env`: `TIER_OVERRIDE`, `N_GPU_LAYERS_OVERRIDE`.

### Chainlit Docker

`Dockerfile.chainlit` — легковесный образ (без torch, llama-cpp, onnx). Копирует только `backend/orchestrator/` и `ums_client.py`. `PYTHONPATH=/app` для абсолютных импортов (`from orchestrator.workflows.compare import ...`).

Аутентификация: `CHAINLIT_ADMIN_USER` / `CHAINLIT_ADMIN_PASSWORD` (env). SQLite persistence: `CHAINLIT_DB_URL`.

## Конфигурация (.env)

**Критические переменные:**
- `MODEL_PATH_QWEN14B` — путь к GGUF-файлу LLM (обязательно)
- `MODEL_PATH_LABSE` — путь к LaBSE (обязательно для workflows и RAG)
- `MODEL_PATH_QWENVL` / `MMPROJ_PATH` — Vision-модель (опционально)
- GPU-слои: `-1` = все на GPU

## Key Development Patterns

### Добавление workflow

1. Создать `backend/orchestrator/workflows/your_workflow.py` с TypedDict state
2. Реализовать nodes как async-функции, возвращающие dict-обновления state
3. Собрать `StateGraph`: `add_node()`, `add_edge()`, `compile()`
4. Зарегистрировать в `chainlit_app.py::_detect_intent()` и добавить handler
5. Для legacy: добавить маршрут в `agent_api.py`

### LLM Prompting

Формат Qwen chat template:
```python
prompt = """<|im_start|>system
You are an expert assistant.<|im_end|>
<|im_start|>user
{user_query}<|im_end|>
<|im_start|>assistant
"""
```

Для structured output: запросить JSON, парсить через `orchestrator/utils.py::parse_json_garbage()`.

### Межсервисное взаимодействие

Все сервисы используют `httpx.AsyncClient`:
```python
async with httpx.AsyncClient(timeout=60.0) as client:
    resp = await client.post(f"{SERVICE_URL}/endpoint", json={...})
    resp.raise_for_status()
    result = resp.json()
```

## Common Issues

**UMS не грузит модели:** проверить пути в `.env`, GPU память (`nvidia-smi`), логи в tmux окне `ums`.

**Порт занят:** `lsof -i :8000` → найти процесс → перезапустить.

**Файлы не находятся в workflows:** проверить `backend/open_webui_uploads/`, Docker bind mount в `docker-compose.yaml`.

**`ModuleNotFoundError` в Chainlit Docker:** убедиться что `ENV PYTHONPATH=/app` в `Dockerfile.chainlit`. Пересобрать: `docker compose build chainlit`.

**tmux не стартует:** проверить conda (`conda env list`), пути в `run_all.sh::find_conda()`.

**Playwright тестирование Chainlit:**
- Авторизация: `admin` / `admin` (из `.env` `CHAINLIT_ADMIN_USER`/`CHAINLIT_ADMIN_PASSWORD`)
- Стандартный `filechooser` event не срабатывает — загружать напрямую: `page.locator('input[type="file"]').first().setInputFiles(path)`
- Для атомарного захвата: `Promise.all([page.waitForEvent('filechooser'), button.click()])` не работает с Chainlit — только прямой setInputFiles

**Hooks (`.claude/hooks/`):**
- Пути в `settings.json` должны быть **абсолютными** — hooks могут запускаться из `backend/`, а не корня проекта
- Шаблон: `"command": "python3 /home/seral/HDD/proj/agent-navigator-pro/.claude/hooks/script.py"`

## Архитектурные принципы

- Orchestrator (chainlit_app / agent_api) управляет маршрутизацией и workflow — не смешивать с логикой сервисов
- MCP-серверы (document, legal) предоставляют специализированные инструменты
- UMS абстрагирует бэкенд моделей (llama-server, st_server)
- RAG pipeline не использует LLM для классификации — только embeddings (EmbeddingIntentClassifier)
