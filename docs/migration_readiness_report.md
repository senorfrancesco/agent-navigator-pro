# Отчёт о готовности к миграции — Agent Navigator Pro

> Дата: 2026-03-30  
> Ветка: `v3.0`  
> Цели миграции: Chainlit → Open WebUI · SQLiteKnowledgeBaseStore → Qdrant · pdfplumber/python-docx → Docling Serve

---

## ОБЛАСТЬ 1: OpenAI-совместимый API

**Файл:** `backend/orchestrator/agent_api.py`  
**Уровень риска:** 🟡 СРЕДНИЙ  
**Точки изменений:** 1 файл, 4 функции  

### Найденные проблемы

#### 1.1 — `agent_api.py:712–718` — SSE-стриминг не соответствует OpenAI spec

**Проблема:** `_stream_openai_compat_response()` не включает обязательные поля `id`, `object`,
`created`, `model` в каждый SSE-chunk. Open WebUI парсит именно эти поля для отображения
стримингового вывода.

```python
# ТЕКУЩИЙ КОД (строки 712–718) — НЕ СООТВЕТСТВУЕТ SPEC
async def _stream_openai_compat_response(execution_response: Dict[str, Any]) -> AsyncGenerator[str, None]:
    yield "data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}) + "\n\n"
    text = str(execution_response.get("assistant_message") or "")
    if text:
        yield "data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]}) + "\n\n"
    yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
    yield "data: [DONE]\n\n"
```

**Требуемое исправление:** добавить `id`, `object`, `created`, `model` в каждый chunk:

```python
# КАК ДОЛЖНО БЫТЬ
async def _stream_openai_compat_response(
    execution_response: Dict[str, Any], *, model: str = "agent-navigator"
) -> AsyncGenerator[str, None]:
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    def _chunk(delta: dict, finish_reason=None) -> str:
        return "data: " + json.dumps({
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }) + "\n\n"
    yield _chunk({"role": "assistant"})
    text = str(execution_response.get("assistant_message") or "")
    if text:
        yield _chunk({"content": text})
    yield _chunk({}, finish_reason="stop")
    yield "data: [DONE]\n\n"
```

**Статус:** 🔴 БЛОКИРУЮЩЕЕ — стриминг в Open WebUI не будет работать без этого.

---

#### 1.2 — `agent_api.py:901` — вызов `_stream_openai_compat_response` без `model`

**Проблема:** При вызове на строке 901 не передаётся `target_model`. После исправления 1.1
нужно обновить сигнатуру вызова:

```python
# СТРОКА 901 — текущий код
async for chunk in _stream_openai_compat_response(response):

# НУЖНО
async for chunk in _stream_openai_compat_response(response, model=target_model):
```

---

#### 1.3 — `agent_api.py:803–817` — `/v1/models` не включает UMS-модели

**Проблема:** Эндпоинт сканирует только `models/gguf/*.gguf` на диске. Не запрашивает
UMS `/status` или реестр. Open WebUI перечисляет все модели через `/v1/models`.

```python
# СТРОКА 803–817 — текущий код
@app.get("/v1/models")
def list_models():
    models = [{"id": "agent-navigator", ...}]
    # только GGUF-файлы, нет UMS-моделей
    return {"object": "list", "data": models}
```

**Требуемое исправление:** добавить async-запрос к UMS `/status` для перечисления
зарегистрированных моделей. Или добавить статичный список из `models_config.py`.  
**Статус:** 🟡 НЕ БЛОКИРУЮЩЕЕ — Open WebUI работает с единственной моделью `agent-navigator`,
но пользователю не будут видны профили.

---

#### 1.4 — `agent_api.py:566–629` — нет `multipart/form-data` эндпоинта для загрузки файлов

**Проблема:** Open WebUI отправляет файлы через `POST /v1/files` с `multipart/form-data`.
Текущая реализация использует только путь-сканирование (`_discover_recent_uploaded_files`,
`_discover_manual_path_attachments`) — это хак-обходной путь, а не spec-совместимый API.

```python
# СТРОКИ 566–628 — директорийный скан как замена file upload
def _discover_recent_uploaded_files(max_age_seconds: int = 600) -> List[FileAttachment]:
    uploads_path = os.path.join(base_dir, "open_webui_uploads")   # сканируем папку
    ...
enable_recent_uploads = os.getenv("OPENAI_COMPAT_ENABLE_UPLOAD_DISCOVERY", "false")  # по умолч. ВЫКЛЮЧЕНО
```

**Требуемое исправление:** добавить эндпоинт `POST /v1/files`:

```python
from fastapi import UploadFile, File as FastAPIFile
import aiofiles

@app.post("/v1/files")
async def upload_file(file: UploadFile = FastAPIFile(...)):
    """Open WebUI file upload endpoint."""
    save_dir = os.path.join(os.path.dirname(__file__), "..", "..", "open_webui_uploads")
    os.makedirs(save_dir, exist_ok=True)
    dest = os.path.join(save_dir, file.filename)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(await file.read())
    file_id = str(uuid.uuid4())
    return {
        "id": file_id,
        "object": "file",
        "filename": file.filename,
        "purpose": "assistants",
        "status": "processed",
        "created_at": int(time.time()),
    }
```

**Статус:** 🔴 БЛОКИРУЮЩЕЕ — без этого файлы от Open WebUI не будут доставляться в pipeline.

---

#### 1.5 — `agent_api.py:713, 717` — `finish_reason` в промежуточных чанках

**Текущий код:** первый chunk (`role` delta) и контентный chunk имеют `"finish_reason": None` ✓  
**Последний chunk:** `"finish_reason": "stop"` ✓  
**Статус:** ✅ ОК — блокирующих проблем нет, но исправление 1.1 меняет структуру.

---

#### 1.6 — `agent_api.py:819–906` — `POST /v1/chat/completions` не принимает `multipart`

**Проблема:** Только `request.json()`. Open WebUI иногда отправляет вложения как multipart.  
**Статус:** 🟡 НЕ БЛОКИРУЮЩЕЕ после добавления `/v1/files`.

---

### Итог по Области 1

| Проблема | Файл:Строка | Тип | Приоритет |
|----------|------------|-----|-----------|
| SSE chunks без `id/object/created/model` | agent_api.py:712–718 | БЛОКИРУЮЩЕЕ | P0 |
| `/v1/files` multipart upload отсутствует | agent_api.py:566 | БЛОКИРУЮЩЕЕ | P0 |
| `_stream_openai_compat_response` вызов без `model` | agent_api.py:901 | БЛОКИРУЮЩЕЕ | P0 |
| `/v1/models` не включает UMS-модели | agent_api.py:803–817 | НЕ БЛОКИРУЮЩЕЕ | P2 |

---

## ОБЛАСТЬ 2: Абстракция Knowledge Base

**Файлы:** `knowledge_base_store.py`, `knowledge_base_retrieval.py`, `knowledge_base_ingestion.py`  
**Уровень риска:** 🟡 СРЕДНИЙ  
**Точки изменений:** 3 файла, ~6 мест  

### 2.1 — Factory function существует ✅

```python
# knowledge_base_store.py:345–350
def get_knowledge_base_store() -> SQLiteKnowledgeBaseStore:
    global _STORE_SINGLETON
    with _STORE_LOCK:
        if _STORE_SINGLETON is None:
            _STORE_SINGLETON = SQLiteKnowledgeBaseStore()
    return _STORE_SINGLETON
```

Factory-функция **существует**. Для замены SQLite→Qdrant достаточно изменить тело `get_knowledge_base_store()`.  
**Проблема:** возвращаемый тип аннотирован как `SQLiteKnowledgeBaseStore`, а не как абстрактный Protocol — нужно будет обновить аннотацию при добавлении `QdrantKnowledgeBaseStore`.

---

### 2.2 — Интерфейсный контракт (Protocol)

Полный контракт, который должен реализовывать `QdrantKnowledgeBaseStore` как drop-in замена:

```python
# ВСТАВИТЬ В: backend/orchestrator/knowledge_base_store.py
from typing import runtime_checkable
from typing import Protocol

@runtime_checkable
class KnowledgeBaseStoreProtocol(Protocol):
    """Контракт, обязательный для любого backend хранилища KB."""

    def register_source_sync(
        self,
        *,
        collection_id: str,
        display_name: str,
        content_hash: str,
        mime_type: str,
        index_version: str,
        embedding_model_id: str,
        chunking_version: str,
        status: str = "indexed",
    ) -> "KnowledgeBaseSourceRecord": ...

    def replace_chunks_sync(
        self,
        *,
        source_id: str,
        chunks: List[Dict[str, Any]],   # chunk_id, chunk_index, text, metadata_json, source_origin, embedding (Optional[np.ndarray])
    ) -> None: ...

    def list_sources_sync(
        self, collection_id: str
    ) -> "List[KnowledgeBaseSourceRecord]": ...

    def list_chunks_sync(
        self,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        include_embeddings: bool = False,
    ) -> "List[KnowledgeBaseChunkRecord]": ...

    # Async-обёртки (используются из chainlit_app.py и agent_api.py)
    async def register_source(self, **kwargs: Any) -> "KnowledgeBaseSourceRecord": ...
    async def replace_chunks(self, *, source_id: str, chunks: List[Dict[str, Any]]) -> None: ...
    async def list_sources(self, collection_id: str) -> "List[KnowledgeBaseSourceRecord]": ...
    async def list_chunks(
        self,
        collection_id: str,
        source_ids: Optional[List[str]] = None,
        include_embeddings: bool = False,
    ) -> "List[KnowledgeBaseChunkRecord]": ...
```

---

### 2.3 — Все call sites `get_knowledge_base_store()`

| Файл | Строка | Контекст |
|------|--------|----------|
| `agent_api.py` | 38 | `import` — импорт функции |
| `agent_api.py` | 452 | передача в `_build_api_execution_dependencies()` как `get_knowledge_base_store=get_knowledge_base_store` |
| `chainlit_app.py` | 66 | `import` — импорт функции |
| `chainlit_app.py` | 1142 | передача в `_build_execution_dependencies()` как `get_knowledge_base_store=get_knowledge_base_store` |
| `knowledge_base_ingestion.py` | 13 | `import` — импорт функции |
| `knowledge_base_ingestion.py` | 44 | `kb_store = store or get_knowledge_base_store()` — прямой вызов |

**Итого: 3 файла, 6 мест.** Для смены backend достаточно изменить тело `get_knowledge_base_store()` в одном месте — все call sites получат новую реализацию автоматически.

---

### 2.4 — `retrieve_merged_chunks()` — параметр `kb_store`

```python
# knowledge_base_retrieval.py:231–244
def retrieve_merged_chunks(
    *,
    query: str,
    rag_scope: str,
    knowledge_collection_id: Optional[str],
    session_docs: Dict[str, Any],
    active_doc_ids: List[str],
    embed_fn: Optional[Callable],
    kb_store: Optional[SQLiteKnowledgeBaseStore],   # ← АРГУМЕНТ, не глобальный импорт ✅
    top_k: int = ...,
    ...
) -> Dict[str, Any]:
```

`kb_store` принимается **как аргумент** — не импортируется глобально. Достаточно передать
`QdrantKnowledgeBaseStore` вместо `SQLiteKnowledgeBaseStore`.  

**Единственная проблема аннотации:** тип параметра `kb_store` на строке 239 аннотирован как
`Optional[SQLiteKnowledgeBaseStore]` — нужно изменить на `Optional[KnowledgeBaseStoreProtocol]`.

---

### 2.5 — Формат хранения эмбеддингов

```python
# knowledge_base_store.py:70–85
def _serialize_embedding(value: Any) -> Optional[bytes]:
    array = np.asarray(value, dtype=np.float32)
    return array.tobytes()   # ← blob в SQLite

def _deserialize_embedding(blob: Optional[bytes], dim: Optional[int]) -> Optional[np.ndarray]:
    return np.frombuffer(blob, dtype=np.float32).copy()   # ← np.ndarray (float32)
```

```python
# knowledge_base_retrieval.py:105–114
for entry in entries:
    embedding = entry.get("embedding")   # np.ndarray (float32) из list_chunks_sync
    embeddings.append(np.asarray(embedding, dtype=np.float32))
if has_precomputed and embeddings:
    retriever.index_with_embeddings(documents, np.vstack(embeddings))   # (N, dim) float32
```

**Вывод:** система везде работает с `np.ndarray` shape `(dim,)` float32.  
`QdrantKnowledgeBaseStore.list_chunks_sync()` должен возвращать `KnowledgeBaseChunkRecord.embedding` как `np.ndarray(float32)` или `None`.  
Qdrant хранит эмбеддинги как `List[float]` — нужна конвертация `np.asarray(qdrant_vector, dtype=np.float32)`.

---

## ОБЛАСТЬ 3: Абстракция парсинга документов

**Файлы:** `mcp_document_server.py`, `equipment.py`, `compare.py`, `document_analysis.py`  
**Уровень риска:** 🟠 ВЫСОКИЙ  
**Точки изменений:** 4 файла, 10 вызовов  

### 3.1 — URL всегда через переменную окружения ✅

`MCP_DOCUMENT_SERVER_URL` задан **в каждом файле** через `os.getenv()` с fallback:

| Файл | Строка | Объявление |
|------|--------|-----------|
| `compare.py` | 24 | `MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")` |
| `equipment.py` | 42 | то же |
| `document_analysis.py` | 46 | то же |
| `agent_api.py` | 636 | `doc_server = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")` (inline) |

Для переключения на Docling Serve достаточно выставить `MCP_DOCUMENT_SERVER_URL=http://docling:5001`.

---

### 3.2 — Карта вызовов эндпоинтов

| Эндпоинт | Файл | Строка | Тело запроса | Ответ |
|----------|------|--------|-------------|-------|
| `POST /load_document` | `compare.py` | 269, 274 | `{"path": str}` | `{"status", "text", "path", "format"}` |
| `POST /load_document` | `document_analysis.py` | 584 | `{"path": str}` | то же |
| `POST /load_document` | `equipment.py` | 963 | `{"path": str}` | то же |
| `POST /load_document` | `agent_api.py` | 644 | `{"path": str}` | то же |
| `POST /load_pages` | `document_analysis.py` | 605 | `{"path": str}` | `{"status", "pages": List[str], "total_pages", "path"}` |
| `POST /extract_tables` | `document_analysis.py` | 622 | `{"path": str}` | `{"status", "tables": [{"page", "data": [[str]]}], "table_count"}` |
| `POST /extract_tables` | `equipment.py` | 881 | `{"path": str, "pages"?: List[int]}` | то же |
| `POST /extract_tables_docx` | `equipment.py` | 883 | `{"path": str}` | то же (page = idx+1) |
| `POST /extract_tables_excel` | `equipment.py` | 885 | `{"path": str, "sheets"?: List[str]}` | `{"status", "tables": [{"page": sheet_name, "data"}], ...}` |
| `POST /smart_chunk` | `equipment.py` | 782 | `{"text": str, "max_tokens": int, "overlap": int}` | `{"status", "chunks": List[str], "chunk_count", ...}` |

**Критично:** все вызовы работают с **путями к файлам** (`"path": str`). Docling Serve работает
иначе: принимает **контент** через `multipart/form-data` или base64. Это структурное различие.

---

### 3.3 — Hardcoded fallbacks, обходящие document server

#### compare.py:279–295 — LOCAL `dc_smart_chunk` вместо `/smart_chunk`

```python
# compare.py:279–295 — HARDCODED fallback, не вызывает document server
def dc_smart_chunk(text: str) -> List[str]:
    chunks = []
    section_pattern = r'\n(?=\d+\.(?:\d+\.)*\s+[А-ЯA])'
    sections = re.split(section_pattern, text)
    for section in sections:
        if len(section) > COMPARE_SECTION_MAX_CHARS:
            parts = re.split(r'\n\s*\n', section)
            for p in parts:
                clean = ' '.join(p.split())
                if len(clean) > COMPARE_MIN_CHUNK_CHARS: chunks.append(clean)
        else:
            clean = ' '.join(section.split())
            if len(clean) > COMPARE_MIN_CHUNK_CHARS: chunks.append(clean)
    return chunks

chunks_old = dc_smart_chunk(text1)   # ← обходит /smart_chunk полностью
chunks_new = dc_smart_chunk(text2)
```

**Оценка:** это намеренное архитектурное решение для compare workflow. После миграции на Docling
документ будет уже получен через Docling и отдельный `/smart_chunk` не нужен — compare.py
получит структурированный текст от адаптера.

#### equipment.py:759–809 — `_split_by_lines()` fallback

```python
# equipment.py:759–809
def _split_by_lines(text: str, max_chars: int) -> List[str]:
    """Fallback: разбивает текст по строкам когда /smart_chunk не справился."""

async def _chunk_text(text: str) -> List[str]:
    if len(text) <= MAX_TEXT_FOR_LLM:
        return [text]   # ← short texts не идут через document server
    try:
        resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/smart_chunk", ...)
        ...
    except Exception as e:
        logger.warning("/smart_chunk failed, using line-based fallback: %s", e)
        return _split_by_lines(text, MAX_TEXT_FOR_LLM)   # ← fallback
```

---

### 3.4 — Стабильность форматов ответа и точки вставки адаптера

Все response shapes достаточно стабильны для прозрачной замены. **Точка вставки адаптера Docling:**

```
Docling Serve                    MCP Document Server (текущий)
────────────────                 ─────────────────────────────
POST /v1/convert/file            → адаптер → POST /load_document
  multipart: file=<bytes>        ←────────── path=<str>
  response: {"status", "output"  → {"status": "success", "text": str, ...}
    {"markdown": str}}
```

**Точка вставки:** создать `DoclingDocumentServerAdapter` — тонкую обёртку,
которая принимает те же запросы (`{"path": str}`), читает файл по пути, передаёт
в Docling Serve, преобразует ответ в текущий формат. Все вызывающие workflow остаются
**без изменений**.

```python
# НОВЫЙ ФАЙЛ: backend/services/document_server/docling_adapter.py
class DoclingDocumentServerAdapter:
    """Adapter: совместим с MCP Document Server API, использует Docling Serve."""

    DOCLING_URL = os.getenv("DOCLING_SERVE_URL", "http://localhost:5001")

    async def load_document(self, path: str) -> Dict[str, Any]:
        ext = Path(path).suffix.lower()
        async with aiofiles.open(path, "rb") as f:
            content = await f.read()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.DOCLING_URL}/v1/convert/file",
                files={"file": (Path(path).name, content)},
            )
        data = resp.json()
        markdown = data.get("output", {}).get("markdown", "")
        return {"status": "success", "text": markdown, "path": path, "format": ext.lstrip(".")}
```

---

## ОБЛАСТЬ 4: Настройки/UI сложность (Chainlit)

**Файл:** `backend/orchestrator/chainlit_app.py`  
**Уровень риска:** 🟢 НИЗКИЙ (Chainlit-специфично, не влияет на API слой)  
**Точки изменений:** 1 файл, 1 функция  

### 4.1 — Все текущие ChatSettings виджеты

Определены в `_send_control_plane_settings()` (строки 1477–1583):

| ID | Тип | Tab | Что делает |
|----|-----|-----|-----------|
| `assistant_mode` | Select (5 вариантов) | use_case | Выбор preset → меняет все остальные настройки |
| `runtime_mode` | Select (3 варианта) | use_case | auto / chat_only / specialized_tasks |
| `rag_scope` | Select (3 варианта) | rag | off / session_rag / knowledge_base_rag |
| `knowledge_collection_id` | TextInput | rag | ID коллекции KB |
| `model_profile` | Select (4 варианта) | model | Логический профиль модели |
| `prompt_profile` | Select (5 вариантов) | prompt | Профиль системного промпта |
| `custom_system_prompt` | TextInput (multiline) | prompt | Переопределение системного промпта |
| `temperature` | Slider 0.0–2.0 | generation | Температура генерации |
| `top_p` | Slider 0.0–1.0 | generation | Top-P sampling |
| `max_tokens` | NumberInput | generation | Максимум токенов |

**Итого: 10 виджетов в 5 вкладках.**

---

### 4.2 — Какие настройки реально читаются в логике выполнения

Все 10 настроек читаются через `_extract_control_plane_state_from_settings()` (строки 1391–1426)
и `_settings_payload_from_input()` (строки 1343–1366), передаются в `OrchestrationRequest` через
`_get_execution_request()` (строки 1280–1305).

```python
# chainlit_app.py:1292–1299 — все настройки используются
payload = {
    "runtime_mode": _get_runtime_mode(),
    "assistant_mode": effective.get("assistant_mode"),
    "rag_scope": effective.get("rag_scope"),
    "knowledge_collection_id": effective.get("knowledge_collection_id"),
    "model_profile": effective.get("model_profile"),
    "prompt_profile": effective.get("prompt_profile"),
    "generation_overrides": dict(effective.get("generation") or {}),
    "custom_system_prompt": effective.get("custom_system_prompt"),
}
```

**Нет мёртвых настроек** — все 10 виджетов реально используются.

Примечание: `tool_scope` — скрытый ключ в control plane state (строки 1300, 1357, 1413),
но **не является** ChatSettings виджетом.

---

### 4.3 — Минимальный набор настроек для сохранения всего роутинга

При переходе на Open WebUI сохранить **только через HTTP headers / metadata поля**:

| Настройка | Как заменить в Open WebUI |
|-----------|--------------------------|
| `assistant_mode` | Через `cl.context.emitter.set_commands` (см. 4.4) |
| `runtime_mode` | Header `X-Runtime-Mode` или system prompt tag |
| `rag_scope` | Header `X-Rag-Scope` или slash-команда |
| `knowledge_collection_id` | Header `X-Knowledge-Collection` или slash-команда |
| `model_profile` | **Нативный выбор модели Open WebUI** (маппинг model → profile) |
| `prompt_profile` | Системный промпт Open WebUI (шаблоны) |
| `custom_system_prompt` | Open WebUI system prompt field |
| `temperature`, `top_p`, `max_tokens` | **Нативные параметры генерации Open WebUI** |

**Минимально необходимые через backend API:**
- `assistant_mode` / `rag_scope` / `knowledge_collection_id` — не покрываются нативным UI

---

### 4.4 — Что заменят `cl.context.emitter.set_commands`

Slash-команды могут заменить:
- `preset:general_chat`, `preset:specific_tasks`, `preset:rag_qa` — через `/preset <mode>`
- `rag_scope:off/session_rag/knowledge_base_rag` — через `/rag <scope>`
- `knowledge_collection_id` — через `/kb <collection_id>`

---

## ОБЛАСТЬ 5: Тест-точки RAG pipeline

**Файлы:** `rag/pipeline.py`, `rag/retriever.py`, `knowledge_base_retrieval.py`  
**Уровень риска:** 🟡 СРЕДНИЙ  
**Точки изменений:** 2 файла (при переходе на Qdrant-retrieval)  

### 5.1 — Граф зависимостей `embed_fn`

```
AdaptiveRAGPipeline.__init__(embed_fn)   [pipeline.py:52]
    │
    ├─→ self.embed_fn = embed_fn         [pipeline.py:78]
    │
    ├─→ HybridRetriever(embed_fn=embed_fn)  [pipeline.py:87]
    │       │
    │       ├─→ self.embed_fn            [retriever.py:141]
    │       ├─→ .index() → embed_fn(documents)          [retriever.py:163]
    │       ├─→ .index_with_embeddings() → skip embed   [retriever.py:173]  ← pre-computed ✅
    │       ├─→ ._search_dense() → embed_fn([query])    [retriever.py:241]
    │       └─→ .rocchio_expand() → embed_fn([query])   [retriever.py:348]
    │
    └─→ EmbeddingIntentClassifier(embed_fn=embed_fn)  [pipeline.py:91]
            └─→ self.embed_fn (используется в classify)

retrieve_merged_chunks(embed_fn=embed_fn)   [knowledge_base_retrieval.py:231]
    │
    ├─→ early return if embed_fn is None: {"chunks": [], "source_scope_summary": "off"}
    │                                                           [retrieval.py:245–246]  ← ПРОБЛЕМА
    └─→ _search_entries(embed_fn=embed_fn)              [retrieval.py:103]
            └─→ HybridRetriever(embed_fn=embed_fn)
                    └─→ retriever.index_with_embeddings() если есть precomputed
                        retriever.index() если нет (вызывает embed_fn)
```

**`embed_fn` полностью передаётся через конструктор** — swappable ✅

---

### 5.2 — `index_with_embeddings()` — поддержка precomputed эмбеддингов ✅

```python
# retriever.py:173–192
def index_with_embeddings(self, documents: List[str], embeddings: np.ndarray):
    """Индексирует документы с уже посчитанными dense embeddings."""
    self.documents = documents
    if self.use_bm25 and self.bm25:
        self.bm25.fit(documents)
    array = np.asarray(embeddings, dtype=np.float32)   # (N, dim)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    self.doc_embeddings = array / norms   # L2-нормализация
    self.indexed = True
```

Используется в `knowledge_base_retrieval.py:114`:
```python
retriever.index_with_embeddings(documents, np.vstack(embeddings))
```

Qdrant уже хранит нормализованные векторы — при Qdrant retrieval `index_with_embeddings`
используется для загрузки векторов из Qdrant в локальный retriever. ✅

---

### 5.3 — Деградация при `embed_fn=None`

| Уровень | Поведение | Строка |
|---------|-----------|--------|
| `retrieve_merged_chunks(embed_fn=None)` | **Возвращает пустой результат** `{"chunks": [], "source_scope_summary": "off"}` | `retrieval.py:245–246` |
| `HybridRetriever(embed_fn=None)._search_dense()` | Возвращает `[]` | `retriever.py:238–239` |
| `HybridRetriever._search_hybrid()` | Падает до BM25-only | `retriever.py:271–274` |
| `AdaptiveRAGPipeline(embed_fn=None)` | Работает в BM25-only режиме | pipeline.py |

**Критичная проблема:** `retrieve_merged_chunks()` на строке 245–246 делает **early return при `embed_fn=None`**, не передаёт управление BM25-only пути внутри `HybridRetriever`. Это блокирует BM25 fallback на уровне KB retrieval.

---

### 5.4 — Прямой retrieval через Qdrant без HybridRetriever

**Текущая архитектура:** `_build_kb_entries()` загружает **все чанки** из SQLite в память,
затем индексирует их в `HybridRetriever` in-process. Это не масштабируется при большом KB.

```python
# knowledge_base_retrieval.py:69–90
def _build_kb_entries(kb_store, collection_id):
    entries = []
    for chunk in kb_store.list_chunks_sync(collection_id, include_embeddings=True):
        # ← ЗАГРУЖАЕТ ВСЁ В ПАМЯТЬ
        entries.append({..., "embedding": chunk.embedding, ...})
    return entries
```

**Путь для Qdrant:** добавить отдельный retrieval path, который вызывает Qdrant `/points/search`
напрямую и возвращает топ-K чанков без загрузки всего индекса в память.

**Точка вставки:** `knowledge_base_retrieval.py:_build_kb_entries()` → добавить альтернативную
`_build_kb_entries_qdrant()` которая выполняет query к Qdrant вместо list_chunks_sync:

```python
# ДОБАВИТЬ в knowledge_base_retrieval.py
def _search_kb_via_qdrant(
    *,
    collection_id: str,
    query_embedding: np.ndarray,
    top_k: int,
    qdrant_url: str,
) -> List[Dict[str, Any]]:
    """Прямой vector search через Qdrant — без загрузки всего KB в память."""
    import httpx
    payload = {
        "vector": query_embedding.tolist(),
        "limit": top_k,
        "with_payload": True,
    }
    resp = httpx.post(
        f"{qdrant_url}/collections/{collection_id}/points/search",
        json=payload,
    )
    results = resp.json().get("result", [])
    return [
        {
            "chunk_id": r["id"],
            "text": r["payload"]["text"],
            "metadata_json": r["payload"].get("metadata_json", {}),
            "source_origin": r["payload"].get("source_origin", "knowledge_base"),
            "display_name": r["payload"].get("display_name", ""),
            "document_id": r["payload"].get("document_id", ""),
            "collection_id": collection_id,
            "raw_score": r["score"],
            "scope_rank": i + 1,
        }
        for i, r in enumerate(results)
    ]
```

---

## Сводная таблица рисков

| Область | Уровень риска | Блокирующие проблемы | Не блокирующие | Файлов | Функций |
|---------|--------------|---------------------|----------------|--------|---------|
| 1 — OpenAI API (Chainlit→Open WebUI) | 🟡 СРЕДНИЙ | SSE fields, `/v1/files` endpoint | `/v1/models` UMS | 1 | 3 |
| 2 — KB Store (SQLite→Qdrant) | 🟡 СРЕДНИЙ | type annotations | BM25 fallback path | 3 | 6 |
| 3 — Doc Parsing (pdfplumber→Docling) | 🟠 ВЫСОКИЙ | path→content handoff | compare.py local chunker | 4 | 10 |
| 4 — UI Settings (Chainlit→Open WebUI) | 🟢 НИЗКИЙ | — | slash-команды | 1 | 1 |
| 5 — RAG pipeline (Qdrant retrieval) | 🟡 СРЕДНИЙ | embed_fn=None early return | Qdrant direct search | 2 | 2 |

---

## Очерёдность изменений (Planning)

### Фаза 1 — Подготовка (выполнить до миграции)

1. **[1.1+1.3] SSE fix** — `agent_api.py:712–718` + `:901` — добавить `id/object/created/model` в SSE chunks + передать `model` при вызове.
2. **[1.4] `/v1/files` endpoint** — добавить `POST /v1/files` в `agent_api.py` с сохранением в `open_webui_uploads/`.
3. **[2.1] Protocol class** — добавить `KnowledgeBaseStoreProtocol` в `knowledge_base_store.py`, обновить аннотации в `retrieve_merged_chunks()`.

### Фаза 2 — Адаптеры

4. **[3.4] Docling adapter** — создать `backend/services/document_server/docling_adapter.py`.
5. **[2.x] QdrantKnowledgeBaseStore** — реализовать по Protocol, подключить через `get_knowledge_base_store()`.
6. **[5.4] Qdrant direct search** — добавить `_search_kb_via_qdrant()` в `knowledge_base_retrieval.py`.

### Фаза 3 — Cleanup

7. **[5.3] BM25 fallback** — исправить `retrieve_merged_chunks()` чтобы BM25-only работал без embed_fn.
8. **[1.3] `/v1/models` UMS** — добавить async-запрос к UMS при листинге моделей.
9. **[4.4] Slash-команды** — добавить `/preset`, `/rag`, `/kb` через `set_commands`.

### Задачи в v3.0 TASKS.md (соответствие)

| Задача отчёта | Соответствующий пункт в TASKS.md |
|---------------|----------------------------------|
| SSE fix (1.1) | B3.53 — Chainlit UX simplification |
| `/v1/files` (1.4) | B3.54 — Open WebUI evaluation plan |
| Docling adapter (3.4) | Не зафиксирован — добавить как новый TD |
| Qdrant store (2.x) | Не зафиксирован — добавить как новый TD |
| BM25 fallback (5.3) | Не зафиксирован — добавить как новый TD |
