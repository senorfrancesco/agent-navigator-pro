"""
Chainlit App — замена Open WebUI для Agent Navigator Pro.

Преимущества:
- cl.Step → каждая LangGraph нода видна как шаг с прогрессом
- Нет встроенного RAG → нет конфликтов
- Файлы не дублируются при follow-up
- Python-native → тот же стек что FastAPI/LangGraph
- Docker-ready: chainlit run app.py --host 0.0.0.0 --port 3000
- Auth: password-based authentication (env-driven)
- History: SQLAlchemy data layer (SQLite) for chat persistence
"""

import asyncio
import logging
import os
import sys
import time
import shutil
import httpx
from typing import Dict, List, Optional, Any

# Добавляем пути (оставляем для обратной совместимости, но используем абсолютные)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chainlit as cl
except ImportError:
    raise ImportError("chainlit not installed. Run: pip install chainlit")

from services.model_manager.ums_client import ums_client
from orchestrator.shared.http_client import get_shared_client

logger = logging.getLogger("chainlit_app")


# === RAG Mode Helper ===

async def _get_rag_mode() -> str:
    """Определяет RAG mode: из env override или из UMS tier config."""
    override = os.getenv("RAG_MODE_OVERRIDE", "auto")
    if override != "auto":
        return override
    try:
        ums_url = os.getenv("UMS_URL", "http://localhost:8090")
        client = await get_shared_client()
        resp = await client.get(f"{ums_url}/status", timeout=5.0)
        data = resp.json()
        return data.get("tier", {}).get("rag_mode", "simple")
    except Exception:
        return "simple"


# === Authentication ===

ADMIN_USER = os.getenv("CHAINLIT_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("CHAINLIT_ADMIN_PASSWORD", "admin")


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> Optional[cl.User]:
    if (username, password) == (ADMIN_USER, ADMIN_PASSWORD):
        return cl.User(
            identifier=username,
            metadata={"role": "admin", "provider": "credentials"},
        )
    return None


# === Data Layer (SQLite persistence) ===

_DB_URL = os.getenv(
    "CHAINLIT_DB_URL",
    "sqlite+aiosqlite:///.data/chainlit.db",
)

try:
    from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

    @cl.data_layer
    def get_data_layer():
        return SQLAlchemyDataLayer(conninfo=_DB_URL)

except ImportError:
    pass  # aiosqlite не установлен — работаем без persistence


# === Session Storage ===

def _get_session_docs() -> Dict[str, Any]:
    docs = cl.user_session.get("documents")
    if docs is None:
        docs = {}
        cl.user_session.set("documents", docs)
    return docs


def _get_session_history() -> List[Dict[str, str]]:
    history = cl.user_session.get("history")
    if history is None:
        history = []
        cl.user_session.set("history", history)
    return history


# === Intent Detection ===

def _detect_intent(query: str, file_count: int = 0, has_session_docs: bool = False) -> str:
    """
    Семантическая классификация интентов (Semantic Router).
    TD-10: Жесткие списки ключевых слов удалены. Вся маршрутизация идет
    через EmbeddingIntentClassifier (поиск ближайших соседей в векторном пространстве).
    """
    query_lower = query.lower()

    # Уровень 1: Semantic Router (Основной и приоритетный)
    # Источник A: classifier из RAG pipeline (после загрузки файлов)
    # Источник B: standalone classifier (pre-initialized в on_chat_start)
    classifier_result = None
    rag = cl.user_session.get("rag_pipeline")
    if rag and rag._classifier_initialized:
        classifier_result = rag.classify_intent(query)
    elif cl.user_session.get("intent_classifier"):
        standalone = cl.user_session.get("intent_classifier")
        try:
            classifier_result = standalone.classify(query)
        except Exception as e:
            logger.warning(f"Standalone classifier error: {e}")

    if classifier_result:
        intent = classifier_result["intent"]
        needs_rag = classifier_result["needs_rag"]

        # Защита: если интент требует файлов, но их нет
        if intent == "document_analysis":
            has_file = file_count >= 1 or (has_session_docs and len(_get_session_docs()) >= 1)
            if not has_file:
                intent = "general_chat"

        if has_session_docs and needs_rag and intent not in ("compare_documents", "equipment_analysis", "document_analysis"):
            intent = "document_question"

        logger.info(f"Semantic Router: intent={intent}, confidence={classifier_result['confidence']:.2f}, margin={classifier_result.get('margin',0):.3f}")
        return intent

    # Уровень 2: Minimal Fallback (только если UMS/Classifier недоступен)
    logger.warning("Semantic Router offline. Using minimal fallback routing.")
    if "сравни" in query_lower or "различия" in query_lower:
        return "compare_documents"
    if "смет" in query_lower or "тз" in query_lower:
        return "equipment_analysis"
    
    single_file = file_count == 1 or (has_session_docs and len(_get_session_docs()) == 1)
    if single_file and ("анализ" in query_lower or "документ" in query_lower):
        return "document_analysis"

    if has_session_docs:
        if "привет" in query_lower or "здравствуй" in query_lower:
            return "greeting"
        return "document_question"

    if "привет" in query_lower or "здравствуй" in query_lower:
        return "greeting"

    return "general_chat"


# === File Loading ===

# Пути для обмена файлами между Docker и хостом
# Внутри контейнера: /app/uploads → на хосте: backend/open_webui_uploads/
UPLOADS_DIR = os.getenv("UPLOADS_DIR", "/app/uploads")
# Хостовый путь, который doc-server на хосте может прочитать
HOST_UPLOADS_DIR = os.getenv("HOST_UPLOADS_DIR", "")


def _to_host_path(container_path: str) -> str:
    """Конвертирует контейнерный путь в хостовый для сервисов на хосте."""
    if HOST_UPLOADS_DIR and container_path.startswith(UPLOADS_DIR):
        return container_path.replace(UPLOADS_DIR, HOST_UPLOADS_DIR, 1)
    return container_path


def _save_to_uploads(src_path: str, filename: str) -> str:
    """Копирует файл в shared uploads директорию, возвращает путь внутри контейнера."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    dst_path = os.path.join(UPLOADS_DIR, filename)
    if src_path != dst_path:
        shutil.copy2(src_path, dst_path)
    return dst_path


async def _load_files(files: List[Dict]) -> List[Dict]:
    """Копирует файлы в shared uploads, загружает через Document Server."""
    doc_server = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
    loaded = []
    client = await get_shared_client()

    for f in files:
        try:
            # Копируем файл в shared директорию
            container_path = _save_to_uploads(f["path"], f["name"])
            host_path = _to_host_path(container_path)

            resp = await client.post(f"{doc_server}/load_document", json={"path": host_path}, timeout=60.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "error":
                    loaded.append({**f, "text": "", "path": container_path, "error": data.get("error")})
                else:
                    text = data.get("text", "")
                    loaded.append({**f, "text": text, "path": container_path})
            else:
                loaded.append({**f, "text": "", "path": container_path, "error": f"HTTP {resp.status_code}"})
        except Exception as e:
            loaded.append({**f, "text": "", "error": str(e)})

    return loaded


# === Prompt Builder ===

MAX_HISTORY_MESSAGES = 10


def _build_prompt(query: str, history: List, system_msg: str = "") -> str:
    if not system_msg:
        system_msg = "Ты помощник Agent Navigator. Помогай пользователю."
    prompt = f"<|im_start|>system\n{system_msg}<|im_end|>\n"
    for msg in history[-MAX_HISTORY_MESSAGES:]:
        prompt += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    prompt += f"<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n"
    return prompt


# === Stream Response ===

async def _stream_response(prompt: str, msg: cl.Message, history: List):
    """Генерация ответа для direct chat без SSE streaming.

    Временный production workaround: direct-chat streaming через
    Chainlit -> UMS -> llama-server даёт GeneratorExit/cancel-scope
    ошибки в httpcore/anyio. Для UI-чата используем обычный infer.
    """
    try:
        response = await asyncio.to_thread(
            ums_client.infer,
            "qwen-14b-llm",
            {"prompt": prompt, "temperature": 0.7},
        )
        msg.content = response.get("choices", [{}])[0].get("text", str(response))
    except Exception:
        try:
            logger.warning("Direct chat infer failed, retrying sync inference", exc_info=True)
            response = ums_client.infer(
                "qwen-14b-llm", {"prompt": prompt, "temperature": 0.7}
            )
            content = response.get("choices", [{}])[0].get("text", str(response))
            msg.content = content
        except Exception as e:
            msg.content = f"Ошибка генерации: {e}"

    await msg.send()
    history.append({"role": "assistant", "content": msg.content})


# === Chainlit Handlers ===

async def _init_classifier():
    """Фоновая инициализация EmbeddingIntentClassifier до загрузки файлов."""
    try:
        from orchestrator.rag.classifier import EmbeddingIntentClassifier
        from services.model_manager.ums_client import create_ums_embed_fn

        embed_fn = await asyncio.to_thread(create_ums_embed_fn)
        if not embed_fn:
            logger.warning("Classifier pre-init: UMS unavailable, skipping")
            return
        classifier = EmbeddingIntentClassifier(embed_fn=embed_fn)
        await asyncio.to_thread(classifier.initialize)
        cl.user_session.set("intent_classifier", classifier)
        logger.info("Classifier pre-initialized successfully")
    except Exception as e:
        logger.warning(f"Classifier pre-init failed: {e}")


@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("documents", {})
    cl.user_session.set("history", [])

    # Фоновая инициализация classifier для раннего semantic routing
    asyncio.create_task(_init_classifier())

    user = cl.user_session.get("user")
    greeting = f", **{user.identifier}**" if user else ""

    await cl.Message(
        content=f"Добро пожаловать{greeting} в **Agent Navigator Pro** v3.0!\n\n"
                "Я помогу вам анализировать юридические документы и сметы.\n\n"
                "**Возможности:**\n"
                "- Загрузите 2 файла и попросите сравнить\n"
                "- Загрузите смету и ТЗ для анализа оборудования\n"
                "- Задайте вопрос по загруженному документу\n"
                "- Или просто поговорите со мной"
    ).send()


@cl.on_chat_resume
async def on_chat_resume(thread):
    """
    Восстановление сессии из сохранённой истории (TD-4 Fix).
    Пытается восстановить список документов и RAG-индекс.
    """
    cl.user_session.set("documents", {})
    history = []
    found_files = []

    if thread and thread.get("steps"):
        for step in thread["steps"]:
            # Восстанавливаем историю сообщений
            if step.get("type") == "user_message":
                history.append({"role": "user", "content": step.get("output", "")})
            elif step.get("type") == "assistant_message":
                history.append({"role": "assistant", "content": step.get("output", "")})
            
            # Ищем информацию о загруженных файлах в шагах
            if step.get("name") == "Загрузка документов" and step.get("output"):
                # Парсим строку "Загружено: file1.pdf, file2.docx"
                output = step.get("output", "")
                if "Загружено:" in output:
                    files_str = output.split("Загружено:")[1].strip()
                    fnames = [f.strip() for f in files_str.split(",") if f.strip()]
                    found_files.extend(fnames)

    cl.user_session.set("history", history)

    # Пытаемся восстановить документы и RAG
    if found_files:
        unique_files = list(set(found_files))
        session_docs = {}
        files_to_load = []
        
        for fname in unique_files:
            # Путь в контейнере
            c_path = os.path.join(UPLOADS_DIR, fname)
            if os.path.exists(c_path):
                files_to_load.append({"name": fname, "path": c_path})
        
        if files_to_load:
            async with cl.Step(name="Восстановление документов", type="tool") as step:
                loaded = await _load_files(files_to_load)
                names = []
                for f in loaded:
                    if f.get("text"):
                        session_docs[f["name"]] = {"text": f["text"], "path": f["path"]}
                        names.append(f["name"])
                cl.user_session.set("documents", session_docs)
                
                # Инициализируем RAG
                if session_docs:
                    from orchestrator.rag.pipeline import AdaptiveRAGPipeline
                    from services.model_manager.ums_client import create_ums_embed_fn
                    
                    embed_fn = create_ums_embed_fn()
                    rag_mode = await _get_rag_mode()
                    rag = AdaptiveRAGPipeline(embed_fn=embed_fn, rag_mode=rag_mode)
                    
                    all_texts = [d["text"] for d in session_docs.values()]
                    all_names = [n for n in session_docs.keys()]
                    await asyncio.to_thread(rag.index_documents, all_texts, doc_names=all_names)
                    cl.user_session.set("rag_pipeline", rag)
                    
                    step.output = f"Восстановлено {len(names)} документов, RAG готов."
                else:
                    step.output = "Не удалось восстановить текст документов."


@cl.on_message
async def on_message(message: cl.Message):
    query = message.content
    history = _get_session_history()
    session_docs = _get_session_docs()

    # Обработка файлов
    new_files = []
    if message.elements:
        for element in message.elements:
            if hasattr(element, 'path') and element.path:
                fname = element.name or os.path.basename(element.path)
                if fname not in session_docs:
                    new_files.append({"name": fname, "path": element.path})

        if new_files:
            async with cl.Step(name="Загрузка документов", type="tool") as step:
                loaded = await _load_files(new_files)
                names = []
                for f in loaded:
                    if f.get("text"):
                        session_docs[f["name"]] = {"text": f["text"], "path": f["path"]}
                        names.append(f["name"])
                    elif f.get("error"):
                        await cl.Message(content=f"Ошибка загрузки {f['name']}: {f['error']}").send()
                step.output = f"Загружено: {', '.join(names)}" if names else "Нет новых файлов"

            # Обновляем new_files с корректными путями из session_docs (не UUID-пути Chainlit)
            new_files = [{"name": n, "path": session_docs[n]["path"]} for n in names if n in session_docs]

    # --- RAG: init or re-index ---
    if new_files and session_docs:
        rag = cl.user_session.get("rag_pipeline")
        if rag is None:
            # Первая загрузка: создаём pipeline
            async with cl.Step(name="Инициализация RAG", type="tool") as step:
                from orchestrator.rag.pipeline import AdaptiveRAGPipeline
                from services.model_manager.ums_client import create_ums_embed_fn

                embed_fn = create_ums_embed_fn()
                rag_mode = await _get_rag_mode()
                search_type = "BM25+Dense (hybrid)" if embed_fn else "BM25-only"

                rag = AdaptiveRAGPipeline(embed_fn=embed_fn, rag_mode=rag_mode)
                all_texts = [d["text"] for d in session_docs.values() if d.get("text")]
                all_names = [n for n, d in session_docs.items() if d.get("text")]
                if all_texts:
                    # TD-3: Выносим тяжелую индексацию в поток, чтобы не блокировать event loop
                    try:
                        await asyncio.to_thread(rag.index_documents, all_texts, doc_names=all_names)
                        step.output = f"RAG: mode={rag_mode}, search={search_type}, indexed {len(all_texts)} docs"
                    except Exception as e:
                        logger.error(f"RAG Indexing failed: {e}")
                        step.output = f"⚠️ RAG Indexing failed: {e}. Falling back to non-RAG mode."
                
                cl.user_session.set("rag_pipeline", rag)
        else:
            # Повторная загрузка: переиндексация
            async with cl.Step(name="Переиндексация", type="tool") as step:
                all_texts = [d["text"] for d in session_docs.values() if d.get("text")]
                all_names = [n for n, d in session_docs.items() if d.get("text")]
                if all_texts:
                    # TD-3: Переиндексация тоже в потоке
                    try:
                        await asyncio.to_thread(rag.index_documents, all_texts, doc_names=all_names)
                        step.output = f"Переиндексировано {len(all_texts)} документов"
                    except Exception as e:
                        logger.error(f"RAG Re-indexing failed: {e}")
                        step.output = f"⚠️ Re-indexing failed: {e}"

    # Роутинг
    intent = _detect_intent(query, len(new_files), has_session_docs=bool(session_docs))

    if intent == "compare_documents":
        await _handle_compare(query, new_files, session_docs)
    elif intent == "equipment_analysis":
        await _handle_equipment(query, new_files, session_docs)
    elif intent == "document_analysis":
        await _handle_document_analysis(query, new_files, session_docs)
    elif intent == "document_question":
        await _handle_doc_question(query, session_docs, history)
    else:
        await _handle_chat(query, session_docs, history)

    history.append({"role": "user", "content": query})


# === Workflow: Сравнение документов ===

# Маппинг нод workflow → отображаемые шаги
COMPARE_NODE_LABELS = {
    "load": ("1. Парсинг документов", "tool"),
    "match": ("2. Сопоставление чанков", "tool"),
    "analyze": ("3. Глубокий анализ", "llm"),
    "report": ("4. Генерация отчёта", "tool"),
}


async def _handle_compare(query: str, new_files: List, session_docs: Dict):
    from orchestrator.workflows.compare import create_compare_graph

    files = list(new_files)[:2]
    if len(files) < 2:
        file_names = list(session_docs.keys())[-2:]
        if len(file_names) < 2:
            await cl.Message(content="Нужно минимум 2 документа для сравнения.").send()
            return
        files = [{"name": n, "path": session_docs[n]["path"]} for n in file_names]

    async with cl.Step(name="Сравнение документов", type="run") as run_step:
        run_step.input = f"{files[0]['name']} ↔ {files[1]['name']}"
        t_start = time.perf_counter()

        try:
            workflow = create_compare_graph()
            initial_state = {
                "input_1": _to_host_path(files[0]["path"]),
                "input_2": _to_host_path(files[1]["path"]),
                "name_1": files[0]["name"],
                "name_2": files[1]["name"],
                "chunks_old": [], "chunks_new": [], "matches": [],
                "analysis_results": [], "final_report": "", "errors": [],
            }

            active_steps = {}
            final_state = {}

            async for event in workflow.astream(initial_state):
                for node_name, output in event.items():
                    final_state.update(output)

                    # Закрываем предыдущий шаг
                    if active_steps:
                        for prev_name, prev_step in list(active_steps.items()):
                            await prev_step.__aexit__(None, None, None)

                    # Обновляем вывод в зависимости от ноды
                    label, step_type = COMPARE_NODE_LABELS.get(
                        node_name, (node_name, "tool")
                    )

                    async with cl.Step(name=label, type=step_type) as step:
                        if node_name == "load":
                            n_old = len(output.get("chunks_old", []))
                            n_new = len(output.get("chunks_new", []))
                            step.output = f"Старый: {n_old} чанков, Новый: {n_new} чанков"

                        elif node_name == "match":
                            matches = output.get("matches", [])
                            n_mod = sum(1 for m in matches if m.get("type") == "MODIFIED")
                            n_add = sum(1 for m in matches if m.get("type") == "ADDED")
                            n_del = sum(1 for m in matches if m.get("type") == "DELETED")
                            step.output = (
                                f"Найдено {len(matches)} изменений: "
                                f"{n_mod} изменено, {n_add} добавлено, {n_del} удалено"
                            )

                        elif node_name == "analyze":
                            results = output.get("analysis_results", [])
                            step.output = f"Проанализировано {len(results)} пар"

                        elif node_name == "report":
                            step.output = "Отчёт сгенерирован"

            elapsed = time.perf_counter() - t_start
            report = final_state.get("final_report", "")
            errors = final_state.get("errors", [])

            if report:
                run_step.output = f"Сравнение завершено за {elapsed:.1f}с"
                await cl.Message(content=report).send()
            elif errors:
                run_step.output = "Завершено с ошибками"
                await cl.Message(content=f"Ошибки:\n" + "\n".join(f"- {e}" for e in errors)).send()
            else:
                await cl.Message(content="Не удалось создать отчёт.").send()

        except Exception as e:
            run_step.output = f"Ошибка: {e}"
            await cl.Message(content=f"Ошибка workflow сравнения: {e}").send()


# === Workflow: Анализ оборудования ===

EQUIPMENT_NODE_LABELS = {
    "extract":  ("1. Извлечение позиций", "tool"),
    "match":    ("2. Сопоставление позиций", "tool"),
    "evaluate": ("3. Оценка соответствия", "llm"),
    "report":   ("4. Генерация отчёта", "tool"),
}

# Ключевые слова для определения mode
_TZ_KEYWORDS = ["тз", "техническое задание", "требовани", "specification", "техзадани"]
_SMETA_KEYWORDS = ["смета", "прайс", "предложение", "кп", "коммерческое"]


def _detect_equipment_mode(file1_name: str, file2_name: str, query: str,
                           text_1: str = "", text_2: str = "") -> str:
    """Эвристика: tz_vs_smeta или smeta_vs_smeta."""
    from orchestrator.workflows.equipment import detect_equipment_mode
    return detect_equipment_mode(file1_name, file2_name, query, text_1, text_2)


async def _handle_equipment(query: str, new_files: List, session_docs: Dict):
    from orchestrator.workflows.equipment import create_equipment_graph

    files = list(new_files)[:2]
    if len(files) < 2:
        file_names = list(session_docs.keys())[-2:]
        if len(file_names) < 2:
            await cl.Message(content="Нужно минимум 2 документа (ТЗ и смета).").send()
            return
        files = [{"name": n, "path": session_docs[n]["path"]} for n in file_names]

    mode = _detect_equipment_mode(
        files[0]["name"], files[1]["name"], query,
        text_1=session_docs.get(files[0]["name"], {}).get("text", "")[:1000],
        text_2=session_docs.get(files[1]["name"], {}).get("text", "")[:1000],
    )

    async with cl.Step(name="Анализ оборудования", type="run") as run_step:
        run_step.input = f"{files[0]['name']} ↔ {files[1]['name']} ({mode})"
        t_start = time.perf_counter()

        try:
            workflow = create_equipment_graph()
            initial_state = {
                "input_1": _to_host_path(files[0]["path"]),
                "input_2": _to_host_path(files[1]["path"]),
                "name_1": files[0]["name"],
                "name_2": files[1]["name"],
                "mode": mode,
                "items_1": [], "items_2": [],
                "matches": [], "analysis_results": [],
                "final_report": "", "errors": [],
                "session_id": "",
            }

            final_state = {}

            async for event in workflow.astream(initial_state):
                for node_name, output in event.items():
                    final_state.update(output)

                    label, step_type = EQUIPMENT_NODE_LABELS.get(
                        node_name, (node_name, "tool")
                    )

                    async with cl.Step(name=label, type=step_type) as step:
                        if node_name == "extract":
                            n1 = len(output.get("items_1", []))
                            n2 = len(output.get("items_2", []))
                            step.output = f"Документ 1: {n1} позиций, Документ 2: {n2} позиций"

                        elif node_name == "match":
                            matches = output.get("matches", [])
                            n_mod = sum(1 for m in matches if m.get("type") not in ("ADDED", "DELETED"))
                            n_add = sum(1 for m in matches if m.get("type") == "ADDED")
                            n_del = sum(1 for m in matches if m.get("type") == "DELETED")
                            step.output = (
                                f"Сопоставлено {len(matches)} пар: "
                                f"{n_mod} совпадений, {n_add} добавлено, {n_del} удалено"
                            )

                        elif node_name == "evaluate":
                            results = output.get("analysis_results", [])
                            n_pass = sum(1 for r in results if r.get("result") in ("PASS", "SAME"))
                            n_fail = sum(1 for r in results if r.get("result") in ("FAIL", "GAP"))
                            step.output = f"Оценено {len(results)} позиций: {n_pass} ОК, {n_fail} несоответствий"

                        elif node_name == "report":
                            step.output = "Отчёт сгенерирован"

            elapsed = time.perf_counter() - t_start
            report = final_state.get("final_report", "")
            errors = final_state.get("errors", [])

            if report:
                run_step.output = f"Анализ завершён за {elapsed:.1f}с"
                await cl.Message(content=report).send()
            elif errors:
                run_step.output = "Завершено с ошибками"
                await cl.Message(content=f"Ошибки:\n" + "\n".join(f"- {e}" for e in errors)).send()
            else:
                await cl.Message(content="Не удалось создать отчёт.").send()

        except Exception as e:
            run_step.output = f"Ошибка: {e}"
            await cl.Message(content=f"Ошибка workflow оборудования: {e}").send()


# === Workflow: Анализ одного документа ===

ANALYSIS_NODE_LABELS = {
    "classify":  ("1. Классификация документа", "tool"),
    "extract":   ("2. Извлечение позиций", "tool"),
    "summarize": ("3. Анализ требований", "llm"),
    "report":    ("4. Генерация отчёта", "tool"),
}


async def _handle_document_analysis(query: str, new_files: List, session_docs: Dict):
    from orchestrator.workflows.document_analysis import create_analysis_graph

    # Берём 1 файл из new_files или из session_docs
    file = None
    if new_files:
        file = new_files[0]
    elif session_docs:
        last_name = list(session_docs.keys())[-1]
        file = {"name": last_name, "path": session_docs[last_name]["path"]}

    if not file:
        await cl.Message(content="Нужно загрузить документ для анализа.").send()
        return

    async with cl.Step(name="Анализ документа", type="run") as run_step:
        run_step.input = file["name"]
        t_start = time.perf_counter()

        try:
            workflow = create_analysis_graph()
            initial_state = {
                "input_path": _to_host_path(file["path"]),
                "doc_name": file["name"],
                "doc_type": "",
                "doc_metadata": {},
                "items": [],
                "full_text": "",
                "summary": "",
                "final_report": "",
                "errors": [],
            }

            final_state = {}

            async for event in workflow.astream(initial_state):
                for node_name, output in event.items():
                    final_state.update(output)

                    label, step_type = ANALYSIS_NODE_LABELS.get(
                        node_name, (node_name, "tool")
                    )

                    async with cl.Step(name=label, type=step_type) as step:
                        if node_name == "classify":
                            doc_type = output.get("doc_type", "?")
                            meta = output.get("doc_metadata", {})
                            step.output = (
                                f"Тип: {doc_type}, "
                                f"страниц: {meta.get('pages', '?')}, "
                                f"символов: {meta.get('chars', '?')}, "
                                f"таблиц: {meta.get('tables_count', '?')}"
                            )

                        elif node_name == "extract":
                            n_items = len(output.get("items", []))
                            step.output = f"Извлечено {n_items} позиций"

                        elif node_name == "summarize":
                            summary = output.get("summary", "")
                            step.output = f"Сводка: {len(summary)} символов"

                        elif node_name == "report":
                            step.output = "Отчёт сгенерирован"

            elapsed = time.perf_counter() - t_start
            report = final_state.get("final_report", "")
            errors = final_state.get("errors", [])

            if report:
                run_step.output = f"Анализ завершён за {elapsed:.1f}с"
                await cl.Message(content=report).send()
            elif errors:
                run_step.output = "Завершено с ошибками"
                await cl.Message(content=f"Ошибки:\n" + "\n".join(f"- {e}" for e in errors)).send()
            else:
                await cl.Message(content="Не удалось создать отчёт.").send()

        except Exception as e:
            run_step.output = f"Ошибка: {e}"
            await cl.Message(content=f"Ошибка workflow анализа: {e}").send()


# === Document Question (RAG) ===

async def _handle_doc_question(query: str, session_docs: Dict, history: List):
    """Вопрос по документам через AdaptiveRAGPipeline с fallback на naive stuffing."""

    rag = cl.user_session.get("rag_pipeline")
    context_text = ""

    if rag and rag._indexed:
        async with cl.Step(name="Поиск по документам", type="retrieval") as step:
            try:
                # embed_fn делает sync HTTP → выносим в thread
                result = await asyncio.to_thread(rag.retrieve, query)
                context_text = result.context_text
                n_chunks = len(result.chunks)
                meta = result.metadata or {}
                intent = result.intent or {}
                step.output = (
                    f"Найдено {n_chunks} релевантных фрагментов "
                    f"(mode: {meta.get('mode', '?')}, "
                    f"intent: {intent.get('intent', '?')}, "
                    f"confidence: {intent.get('confidence', 0):.2f})"
                )
            except Exception as e:
                logger.warning(f"RAG retrieve failed, falling back to naive: {e}")
                context_text = ""  # fallback ниже

    # Fallback: naive context stuffing (RAG не инициализирован, не проиндексирован, или упал)
    if not context_text and session_docs:
        async with cl.Step(name="Контекст документов", type="retrieval") as step:
            total = 0
            max_chars = 16000
            for fname, doc_info in session_docs.items():
                text = doc_info.get("text", "")
                remaining = max_chars - total
                if remaining <= 0:
                    break
                if len(text) > remaining:
                    text = text[:remaining] + "..."
                context_text += f"--- Документ: {fname} ---\n{text}\n\n"
                total += len(text)
            step.output = f"Контекст из {len(session_docs)} документов ({total} символов)"

    if not context_text:
        await cl.Message(content="Документы не содержат текста для анализа.").send()
        return

    system_msg = (
        "Ты помощник Agent Navigator. Отвечай на вопросы по документам.\n\n"
        f"Контекст документов:\n{context_text}"
    )

    prompt = _build_prompt(query, history, system_msg)
    msg = cl.Message(content="")
    await _stream_response(prompt, msg, history)


# === General Chat ===

GENERAL_CHAT_SYSTEM = (
    "Ты — Agent Navigator, специализированный ИИ-ассистент для анализа юридических документов и технических смет. "
    "Твои основные возможности:\n"
    "- Сравнение двух документов (договоров, технических заданий, редакций) с выявлением изменений\n"
    "- Анализ соответствия сметы или КП техническому заданию (ТЗ vs смета/КП)\n"
    "- Ответы на вопросы по содержимому загруженных документов\n"
    "- Поиск конкретных условий, цифр и требований в документах\n\n"
    "Ты НЕ умеешь: искать в интернете, давать прогнозы погоды, курсы валют, новости. "
    "Если пользователь спрашивает о чём-то за пределами твоих возможностей — вежливо объясни, "
    "что специализируешься на анализе документов, и предложи загрузить файлы для работы."
)


async def _handle_chat(query: str, session_docs: Dict, history: List):
    # Semantic Router определил needs_rag=False → отвечаем без контекста документов.
    # Даже если документы загружены — greeting/general_chat не нуждаются в RAG.
    prompt = _build_prompt(query, history, GENERAL_CHAT_SYSTEM)
    msg = cl.Message(content="")
    await _stream_response(prompt, msg, history)
