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
import asyncio
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
    """Получает документы текущей сессии."""
    docs = cl.user_session.get("documents")
    if docs is None:
        docs = {}
        cl.user_session.set("documents", docs)
    return docs


def _get_session_history() -> List[Dict[str, str]]:
    """Получает историю сообщений сессии."""
    history = cl.user_session.get("history")
    if history is None:
        history = []
        cl.user_session.set("history", history)
    return history


# === Intent Detection ===

def _detect_intent(query: str, files: List = None) -> str:
    """Простое определение интента по ключевым словам."""
    query_lower = query.lower()

    if any(kw in query_lower for kw in ["сравни", "различия", "изменения"]):
        return "compare_documents"
    if any(kw in query_lower for kw in ["смета", "оборудование", "тз", "закупка"]):
        return "equipment_analysis"
    if any(kw in query_lower for kw in [
        "из документа", "в файле", "что написано", "найди в",
        "по документу", "согласно", "в тексте"
    ]):
        return "document_question"
    if any(kw in query_lower for kw in ["привет", "здравствуй", "добрый"]):
        return "greeting"

    return "general_chat"


# === Chainlit Handlers ===

@cl.on_chat_start
async def on_chat_start():
    """Инициализация новой сессии."""
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
    """Обработка входящего сообщения."""
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
                loaded = []
                for f in new_files:
                    try:
                        import httpx
                        doc_server = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
                        async with httpx.AsyncClient(timeout=60.0) as client:
                            resp = await client.post(f"{doc_server}/load_document", json={"path": f["path"]})
                            if resp.status_code == 200:
                                text = resp.json().get("text", "")
                                session_docs[f["name"]] = {"text": text, "path": f["path"]}
                                loaded.append(f["name"])
                    except Exception as e:
                        await cl.Message(content=f"Ошибка загрузки {f['name']}: {e}").send()

                step.output = f"Загружено: {', '.join(loaded)}" if loaded else "Нет новых файлов"

    # Определяем интент
    intent = _detect_intent(query, new_files)
    new_file_count = len(new_files)

    # === Роутинг ===

    if intent == "compare_documents" and new_file_count >= 2:
        await _handle_compare(query, new_files, session_docs)

    elif intent == "equipment_analysis" and new_file_count >= 2:
        await _handle_equipment(query, new_files, session_docs)

    elif intent == "document_question" and session_docs:
        await _handle_doc_question(query, session_docs, history)

    else:
        await _handle_chat(query, session_docs, history)

    # Сохраняем в историю
    history.append({"role": "user", "content": query})


async def _handle_compare(query: str, new_files: List, session_docs: Dict):
    """Workflow сравнения документов."""
    from orchestrator.workflows.compare import create_compare_graph

    files = list(new_files)[:2]

    async with cl.Step(name="Сравнение документов", type="run") as run_step:
        # Шаг 1: Загрузка
        async with cl.Step(name="1. Парсинг документов", type="tool") as s1:
            s1.output = f"Документы: {files[0]['name']}, {files[1]['name']}"

        # Шаг 2: Запуск workflow
        async with cl.Step(name="2. Сопоставление чанков", type="tool") as s2:
            try:
                workflow = create_compare_graph()
                initial_state = {
                    "input_1": files[0]["path"],
                    "input_2": files[1]["path"],
                    "chunks_old": [], "chunks_new": [], "matches": [],
                    "analysis_results": [], "final_report": "", "errors": [],
                }

                final_state = {}
                async for event in workflow.astream(initial_state):
                    for node_name, output in event.items():
                        final_state.update(output)
                        if node_name == "batch_match_node":
                            matches = output.get("matches", [])
                            s2.output = f"Найдено {len(matches)} совпадений"

                # Шаг 3: Анализ
                async with cl.Step(name="3. Глубокий анализ", type="tool") as s3:
                    analysis = final_state.get("analysis_results", [])
                    s3.output = f"Проанализировано {len(analysis)} пар"

                # Шаг 4: Отчёт
                report = final_state.get("final_report", "")
                if report:
                    run_step.output = "Сравнение завершено"
                    await cl.Message(content=report).send()
                else:
                    errors = final_state.get("errors", [])
                    await cl.Message(content=f"Ошибки: {'; '.join(errors) if errors else 'Неизвестная ошибка'}").send()

            except Exception as e:
                await cl.Message(content=f"Ошибка workflow: {e}").send()


async def _handle_equipment(query: str, new_files: List, session_docs: Dict):
    """Workflow анализа оборудования."""
    from orchestrator.workflows.equipment import create_equipment_graph

    files = list(new_files)[:2]

    async with cl.Step(name="Анализ оборудования", type="run") as run_step:
        try:
            workflow = create_equipment_graph()
            tz_path = files[0]["path"]
            smeta_path = files[1]["path"]

            initial_state = {
                "input_tz": tz_path, "input_smeta": smeta_path,
                "requirements": [], "offers": [], "matches": [],
                "final_report": "", "errors": [],
            }

            final_state = {}
            async for event in workflow.astream(initial_state):
                for node_name, output in event.items():
                    final_state.update(output)

            report = final_state.get("final_report", "")
            if report:
                run_step.output = "Анализ завершён"
                await cl.Message(content=report).send()
            else:
                errors = final_state.get("errors", [])
                await cl.Message(content=f"Ошибки: {'; '.join(errors)}").send()

        except Exception as e:
            await cl.Message(content=f"Ошибка: {e}").send()


async def _handle_doc_question(query: str, session_docs: Dict, history: List):
    """Вопрос по документам с RAG."""
    # Собираем контекст из документов
    doc_context = ""
    total = 0
    max_chars = 16000
    for fname, doc_info in session_docs.items():
        text = doc_info.get("text", "")
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining] + "..."
        doc_context += f"--- Документ: {fname} ---\n{text}\n\n"
        total += len(text)

    system_msg = f"Ты помощник Agent Navigator. Отвечай на вопросы по документам.\n\nКонтекст документов:\n{doc_context}"

    # Строим промпт
    prompt = f"<|im_start|>system\n{system_msg}<|im_end|>\n"
    for msg in history[-10:]:
        prompt += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    prompt += f"<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n"

    # Стриминг ответа
    msg = cl.Message(content="")
    async with cl.Step(name="Поиск по документам", type="retrieval") as step:
        step.output = f"Контекст из {len(session_docs)} документов ({total} символов)"

    try:
        async for token in ums_client.async_infer_stream("qwen-14b-llm", {"prompt": prompt, "temperature": 0.7}):
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


async def _handle_chat(query: str, session_docs: Dict, history: List):
    """Обычный чат со стримингом."""
    system_msg = "Ты помощник Agent Navigator. Помогай пользователю."

    prompt = f"<|im_start|>system\n{system_msg}<|im_end|>\n"
    for msg in history[-10:]:
        prompt += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    prompt += f"<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n"

    msg = cl.Message(content="")
    try:
        async for token in ums_client.async_infer_stream("qwen-14b-llm", {"prompt": prompt, "temperature": 0.7}):
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
