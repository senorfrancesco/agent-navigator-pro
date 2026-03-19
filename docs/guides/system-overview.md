# Как работает система — обзор

Общее руководство по архитектуре, компонентам и рабочему процессу Agent Navigator Pro.

---

## Что это такое

**Agent Navigator Pro** — агентная система для анализа юридических документов и технических спецификаций. Принимает PDF/DOCX файлы, понимает запрос пользователя и запускает нужный pipeline:

- обычный чат с локальной LLM
- вопрос по документу (RAG)
- анализ одного документа
- сравнение двух юридических документов
- сравнение ТЗ и коммерческого предложения (анализ оборудования)

---

## Схема компонентов

```
Пользователь
     │
     ▼
┌──────────────────────────────────────────────────────┐
│  Chainlit UI  (Docker, порт 3000)                    │
│  - чат-интерфейс                                     │
│  - загрузка файлов                                   │
│  - отображение шагов workflow (cl.Step)              │
└──────────────────────┬───────────────────────────────┘
                       │ http / локальная сеть
                       ▼
┌──────────────────────────────────────────────────────┐
│  Backend (хост, tmux-сессия agent-navigator)         │
│                                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │  orchestration_runtime.py                   │    │
│  │  — decide_orchestration()                   │    │
│  │  — определяет intent, выбирает route        │    │
│  └──────────────────┬──────────────────────────┘    │
│                     │                                │
│  ┌──────────────────▼──────────────────────────┐    │
│  │  execution_runtime.py                       │    │
│  │  — execute_orchestration()                  │    │
│  │  — запускает нужный executor                │    │
│  └──┬──────────┬──────────┬────────────────────┘    │
│     │          │          │                          │
│     ▼          ▼          ▼                          │
│  LangGraph  AdaptiveRAG  UMS client                 │
│  Workflows  Pipeline     (http → порт 8090)          │
│  compare    BM25+Dense                               │
│  equipment  RRF Fusion                               │
│                                                      │
│  ┌─────────────┐  ┌─────────────┐                   │
│  │ Doc Server  │  │Legal Server │                   │
│  │ порт 8001   │  │ порт 8002   │                   │
│  │ PDF/DOCX    │  │ batch_match │                   │
│  │ парсинг OCR │  │ embeddings  │                   │
│  └─────────────┘  └─────────────┘                   │
│                                                      │
│  ┌──────────────────────────────────┐               │
│  │  Unified Model Server (UMS)      │               │
│  │  порт 8090                       │               │
│  │  — управляет жизненным циклом    │               │
│  │    моделей                       │               │
│  │  — Qwen-14B → llama-server :8091 │               │
│  │  — LaBSE ONNX → порт 8093       │               │
│  │  — hardware profiling → TierSel  │               │
│  └──────────────────────────────────┘               │
└──────────────────────────────────────────────────────┘

Shared storage: backend/open_webui_uploads/
  (Docker bind-mount → /app/uploads внутри Chainlit)
```

---

## Компоненты подробнее

### Chainlit UI (Docker)

Фронтенд. Работает в Docker-контейнере на порту 3000.

- Принимает сообщения и файлы от пользователя
- Определяет что нужно сделать (`_detect_intent`)
- Запускает нужный backend-path и показывает шаги (cl.Step)
- Сохраняет историю чата в SQLite

Код: `backend/orchestrator/chainlit_app.py`

### Orchestration Runtime

Мозг системы. Принимает запрос и решает что делать.

- `decide_orchestration()` — анализирует intent, файлы, режим
- `choose_route()` — выбирает: chat / doc_question / compare / equipment / document_analysis
- Социальный guard — отличает "привет" от реального запроса
- Ambiguity routing — если неясно, спрашивает пользователя

Код: `backend/orchestrator/orchestration_runtime.py`

### Execution Runtime

Исполнитель. Получает route и запускает нужный код.

- 6 executor'ов: general_chat, doc_question, compare, equipment, document_analysis, choose_route
- `ExecutionDependencies` — DI-контейнер с 26 инъектируемыми callable (инференс, RAG, отчёты)
- Пишет состояние в backend state store (SQLite)

Код: `backend/orchestrator/execution_runtime.py`

### UI Control Plane

Управление конфигурацией.

- 5 режимов ассистента: general_chat, coding, agentic, specific_tasks, rag_qa
- 5 вкладок в Chainlit: Use Case, RAG, Model, Prompt, Generation
- Цепочка приоритетов: hard defaults → preset → UX overrides → enforced → clamping

Код: `backend/orchestrator/ui_control_plane.py`

### LangGraph Workflows

Сложные многошаговые задачи.

**compare.py** — сравнение документов:
```
load_documents → batch_match (LaBSE embeddings) → deep_compare (LLM) → generate_report
```

**equipment.py** — анализ ТЗ/КП:
```
load_documents → detect_mode → parse_items → match_items (LaBSE) → deep_compare → generate_report
```

State — TypedDict, каждый node возвращает dict-обновления. Ошибки накапливаются в `state["errors"]`.

Код: `backend/orchestrator/workflows/`

### Adaptive RAG Pipeline

Поиск по документам. Четыре уровня сложности:

| Tier | Режим | Когда |
|------|-------|-------|
| 1 | simple | Простые вопросы, быстрый ответ |
| 2 | corrective | Средняя сложность, нужна проверка качества |
| 3 | agentic | Сложные запросы, до 3 итераций переформулировки |
| 4 | multi-agent | Планируется |

Retrieval: BM25 (русские стоп-слова) + Dense (LaBSE) → RRF Fusion → Z-score grading → top-k.

Код: `backend/orchestrator/rag/`

### Unified Model Server (UMS)

Менеджер моделей. Порт 8090.

- При старте: hardware profiling → GPU/VRAM → TierSelector → вычисляет `gpu_layers`, `ctx_size`
- Предзагружает Qwen-14B в локальный `llama-server` или цепляет внешний `vLLM` adapter, если `BACKEND_MODE=vllm`
- Один тяжёлый GGUF в памяти — при смене выгружает предыдущий
- `/v1/embeddings` — LaBSE ONNX (CPU, 1.2-1.4x быстрее SentenceTransformers)
- `/status` — публикует runtime_profile, context budget, loaded models

Код: `backend/services/model_manager/unified_model_server.py`

### Document Server (порт 8001)

- `/load_document` — парсит PDF/DOCX, OCR для сканов, возвращает текст
- `/chunk_text` — разбивает на чанки с учётом структуры юридических документов

### Legal Server (порт 8002)

- `/batch_match` — batch-матчинг строк через LaBSE embeddings (cosine similarity)

### Agent API (порт 8000)

OpenAI-compatible entrypoint для внешних клиентов и Open WebUI.

- `/v1/chat/completions` — stream и non-stream
- `/orchestrate` — только routing (без исполнения)
- `/execute_orchestration` — полный цикл
- `/health` — health-check

---

## Поток данных — пример: сравнение документов

```
1. Пользователь загружает 2 PDF в Chainlit
2. chainlit_app.py → _detect_intent() → file_count=2, "сравни" → compare_documents
3. HTTP POST → Doc Server /load_document (2 раза) → текст документов
4. LangGraph compare workflow запускается:
   a. batch_match_node: Legal Server /batch_match → LaBSE similarities
   b. deep_compare_node: UMS /infer → Qwen-14B анализирует различия
   c. generate_report_node: формирует markdown-отчёт
5. Отчёт сохраняется в backend/open_webui_uploads/Report_Compare_*.md
6. Chainlit показывает результат через cl.Step
```

---

## Гибридный режим GPU/CPU

Система намеренно разделяет нагрузку:

| Компонент | Где работает | Почему |
|-----------|-------------|--------|
| Qwen-14B inference | GPU (VRAM) | Требует высокой пропускной способности |
| LaBSE ONNX embeddings | CPU | Освобождает VRAM для LLM |
| Qwen3-Embedding-0.6B | CPU | То же, классификация интентов |
| BM25 поиск | CPU | Не нужен GPU |

Параметры в `.env`:
```bash
N_GPU_LAYERS_QWEN14B=-1   # -1 = все слои на GPU
N_GPU_LAYERS_QWEN14B=0    # 0 = CPU only (медленно, но работает)
```

---

## Рабочий процесс разработки

```bash
# 1. Активировать окружение
conda activate diploma_llm

# 2. Запустить систему
./scripts/launcher.sh --target native

# 3. Подключиться к tmux
tmux attach -t agent-navigator

# 4. Изменить код backend-сервиса
# → сервис перезапускается автоматически (uvicorn --reload)
# → или перезапустить вручную в нужном окне tmux

# 5. Изменить chainlit_app.py
# → требует пересборки Docker
docker compose build chainlit && docker compose up -d chainlit

# 6. Запустить тесты
cd backend && pytest tests/ -v -m "not integration"

# 7. Остановить всё
./scripts/stop_all.sh
```

---

## Файловая структура ключевых модулей

```
backend/orchestrator/
├── chainlit_app.py          ← UI entrypoint, _detect_intent
├── orchestration_runtime.py ← decide_orchestration, routing
├── execution_runtime.py     ← execute_orchestration, 6 executors
├── ui_control_plane.py      ← конфиг, профили, precedence chain
├── agent_api.py             ← OpenAI-compatible HTTP API
├── state_store.py           ← backend state (SQLite)
├── rag/
│   ├── pipeline.py          ← AdaptiveRAGPipeline
│   ├── retriever.py         ← BM25 + Dense + RRF
│   ├── classifier.py        ← EmbeddingIntentClassifier
│   └── chunker.py           ← юридический чанкер
└── workflows/
    ├── compare.py           ← LangGraph: сравнение документов
    └── equipment.py         ← LangGraph: ТЗ/КП анализ

backend/services/
├── model_manager/
│   └── unified_model_server.py  ← UMS
├── document_server/
│   └── mcp_document_server.py
└── legal_server/
    └── mcp_legal_server.py
```
