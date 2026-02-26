"""
Chainlit App — замена Open WebUI для Agent Navigator Pro.

Преимущества:
- cl.Step → каждая LangGraph нода видна как шаг с прогрессом
- Нет встроенного RAG → нет конфликтов
- Файлы не дублируются при follow-up
- Python-native → тот же стек что FastAPI/LangGraph
- Docker-ready: chainlit run app.py --host 0.0.0.0 --port 3000
"""

import os
import sys
import time
from typing import Dict, List, Optional, Any

# Добавляем пути
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chainlit as cl
except ImportError:
    raise ImportError("chainlit not installed. Run: pip install chainlit")

from services.model_manager.ums_client import ums_client


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

COMPARE_KEYWORDS = ["сравни", "различия", "изменения", "отличия", "сопоставь"]
EQUIPMENT_KEYWORDS = ["смета", "оборудование", "тз", "закупка", "спецификация"]
DOC_KEYWORDS = ["из документа", "в файле", "что написано", "найди в",
                "по документу", "согласно", "в тексте"]
GREETING_KEYWORDS = ["привет", "здравствуй", "добрый"]


def _detect_intent(query: str, file_count: int = 0) -> str:
    query_lower = query.lower()

    if file_count >= 2:
        if any(kw in query_lower for kw in COMPARE_KEYWORDS):
            return "compare_documents"
        if any(kw in query_lower for kw in EQUIPMENT_KEYWORDS):
            return "equipment_analysis"

    if any(kw in query_lower for kw in DOC_KEYWORDS):
        return "document_question"
    if any(kw in query_lower for kw in GREETING_KEYWORDS):
        return "greeting"

    return "general_chat"


# === File Loading ===

async def _load_files(files: List[Dict]) -> List[Dict]:
    """Загружает файлы через Document Server, возвращает список с текстом."""
    import httpx
    doc_server = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
    loaded = []

    for f in files:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{doc_server}/load_document", json={"path": f["path"]})
                if resp.status_code == 200:
                    text = resp.json().get("text", "")
                    loaded.append({**f, "text": text})
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
    """Стриминг ответа LLM с fallback на sync."""
    try:
        async for token in ums_client.async_infer_stream(
            "qwen-14b-llm", {"prompt": prompt, "temperature": 0.7}
        ):
            await msg.stream_token(token)
    except Exception:
        try:
            response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.7})
            content = response.get("choices", [{}])[0].get("text", str(response))
            msg.content = content
        except Exception as e:
            msg.content = f"Ошибка генерации: {e}"

    await msg.send()
    history.append({"role": "assistant", "content": msg.content})


# === Chainlit Handlers ===

@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("documents", {})
    cl.user_session.set("history", [])
    await cl.Message(
        content="Добро пожаловать в **Agent Navigator Pro** v3.0!\n\n"
                "Я помогу вам анализировать юридические документы и сметы.\n\n"
                "**Возможности:**\n"
                "- Загрузите 2 файла и попросите сравнить\n"
                "- Загрузите смету и ТЗ для анализа оборудования\n"
                "- Задайте вопрос по загруженному документу\n"
                "- Или просто поговорите со мной"
    ).send()


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

    # Роутинг
    intent = _detect_intent(query, len(new_files))

    if intent == "compare_documents":
        await _handle_compare(query, new_files, session_docs)
    elif intent == "equipment_analysis":
        await _handle_equipment(query, new_files, session_docs)
    elif intent == "document_question" and session_docs:
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
                "input_1": files[0]["path"],
                "input_2": files[1]["path"],
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
                            prev_step.__aexit__(None, None, None)

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
                            n_mod = sum(1 for m in matches if m.get("status") == "MODIFIED")
                            n_add = sum(1 for m in matches if m.get("status") == "ADDED")
                            n_del = sum(1 for m in matches if m.get("status") == "DELETED")
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
    "extract": ("1. Извлечение данных", "tool"),
    "evaluate": ("2. Оценка соответствия", "llm"),
    "report": ("3. Генерация отчёта", "tool"),
}


async def _handle_equipment(query: str, new_files: List, session_docs: Dict):
    from orchestrator.workflows.equipment import create_equipment_graph

    files = list(new_files)[:2]
    if len(files) < 2:
        file_names = list(session_docs.keys())[-2:]
        if len(file_names) < 2:
            await cl.Message(content="Нужно минимум 2 документа (ТЗ и смета).").send()
            return
        files = [{"name": n, "path": session_docs[n]["path"]} for n in file_names]

    async with cl.Step(name="Анализ оборудования", type="run") as run_step:
        run_step.input = f"ТЗ: {files[0]['name']}, Смета: {files[1]['name']}"
        t_start = time.perf_counter()

        try:
            workflow = create_equipment_graph()
            initial_state = {
                "input_tz": files[0]["path"],
                "input_smeta": files[1]["path"],
                "requirements": [], "offers": [], "matches": [],
                "final_report": "", "errors": [],
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
                            n_req = len(output.get("requirements", []))
                            n_off = len(output.get("offers", []))
                            step.output = f"Требования: {n_req}, Предложения: {n_off}"

                        elif node_name == "evaluate":
                            matches = output.get("matches", [])
                            n_pass = sum(1 for m in matches if m.get("result") == "PASS")
                            n_fail = sum(1 for m in matches if m.get("result") == "FAIL")
                            step.output = f"Оценено {len(matches)} позиций: {n_pass} ОК, {n_fail} несоответствий"

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


# === Document Question (RAG) ===

async def _handle_doc_question(query: str, session_docs: Dict, history: List):
    """Вопрос по документам через AdaptiveRAGPipeline."""

    # Пытаемся использовать RAG pipeline если проиндексирован
    rag = cl.user_session.get("rag_pipeline")
    context_text = ""

    if rag:
        async with cl.Step(name="Поиск по документам", type="retrieval") as step:
            result = rag.retrieve(query)
            context_text = result.context_text
            n_chunks = len(result.chunks)
            intent = result.intent or {}
            step.output = (
                f"Найдено {n_chunks} релевантных фрагментов "
                f"(intent: {intent.get('intent', '?')}, "
                f"confidence: {intent.get('confidence', 0):.2f})"
            )
    else:
        # Fallback: naive context stuffing
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

    system_msg = (
        "Ты помощник Agent Navigator. Отвечай на вопросы по документам.\n\n"
        f"Контекст документов:\n{context_text}"
    )

    prompt = _build_prompt(query, history, system_msg)
    msg = cl.Message(content="")
    await _stream_response(prompt, msg, history)


# === General Chat ===

async def _handle_chat(query: str, session_docs: Dict, history: List):
    prompt = _build_prompt(query, history)
    msg = cl.Message(content="")
    await _stream_response(prompt, msg, history)
