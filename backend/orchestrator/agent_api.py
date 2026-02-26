"""
Agent API - Умный оркестратор на базе LangGraph.
Поддерживает микросервисную архитектуру, динамический список моделей и OpenAI-совместимый API.
"""

import asyncio
import json
import hashlib
import uuid
import time
import sys
import os
import re
import warnings
from typing import Dict, Any, Optional, List, AsyncGenerator, Tuple
from dotenv import load_dotenv

# Подавление предупреждений pynvml
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

# Загрузка переменных окружения
load_dotenv()
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx

# Подключаем пути
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from orchestrator.workflows.compare import create_compare_graph
from orchestrator.workflows.equipment import create_equipment_graph

try:
    from services.resource_monitor import get_system_resources
except ImportError:
    def get_system_resources(): return {"error": "Resource monitor not found"}

app = FastAPI(title="Agent Navigator Pro Orchestrator", version="2.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Сессионный менеджер документов ===
sessions: Dict[str, Dict[str, Any]] = {}

# Дедупликация параллельных запросов от Open WebUI
# Хранит dedup_key -> timestamp начала обработки
_active_workflows: Dict[str, float] = {}
DEDUP_WINDOW_SEC = 30  # Окно дедупликации (workflow может длиться >60 сек)

# === Pydantic Models ===

class FileAttachment(BaseModel):
    name: str
    path: str
    size: int
    type: str

class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    attachments: Optional[List[FileAttachment]] = None
    model_settings: Optional[Dict[str, Any]] = None

# === Document Order Detection (Задача 1) ===

def _compute_files_hash(file_paths: List[str]) -> str:
    """Вычисляет хеш на основе путей и размеров файлов для дедупликации."""
    h = hashlib.md5()
    for p in sorted(file_paths):
        h.update(p.encode())
        try:
            h.update(str(os.path.getsize(p)).encode())
        except OSError:
            pass
    return h.hexdigest()

def _extract_document_date(text: str) -> Optional[float]:
    """
    Извлекает дату документа из первых ~1000 символов текста.
    Ищет типичные паттерны юридических документов:
    - "30 июня 2023 г."
    - "24 мая 2021 г."
    - "от 17.07.2018"
    - "25.05.2021"
    - "2023-06-30"
    Returns: timestamp (float) или None
    """
    header = text[:1500]

    MONTHS_RU = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
        'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
        'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
    }

    # Паттерн: "30 июня 2023 г." / "24 мая 2021 г."
    ru_date = re.search(r'(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})', header)
    if ru_date:
        day, month_name, year = int(ru_date.group(1)), MONTHS_RU[ru_date.group(2)], int(ru_date.group(3))
        try:
            return datetime(year, month_name, day).timestamp()
        except ValueError:
            pass

    # Паттерн: "25.05.2021" или "от 17.07.2018"
    dot_date = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', header)
    if dot_date:
        day, month, year = int(dot_date.group(1)), int(dot_date.group(2)), int(dot_date.group(3))
        try:
            return datetime(year, month, day).timestamp()
        except ValueError:
            pass

    # Паттерн: "2023-06-30"
    iso_date = re.search(r'(\d{4})-(\d{2})-(\d{2})', header)
    if iso_date:
        year, month, day = int(iso_date.group(1)), int(iso_date.group(2)), int(iso_date.group(3))
        try:
            return datetime(year, month, day).timestamp()
        except ValueError:
            pass

    return None

def determine_document_order(
    attachments: List[FileAttachment],
    query: str,
    session_docs: Optional[Dict[str, Any]] = None
) -> Tuple[FileAttachment, FileAttachment, str]:
    """
    Определяет порядок документов: старый (old) и новый (new).

    Приоритет:
    1. Явные указания в запросе: [старый] файл1, [новый] файл2
    2. Эвристика по именам файлов: v1/v2, old/new, даты, номера редакций
    3. Парсинг дат из содержимого документов (первая страница)
    4. Fallback: mtime файлов (более ранний = old)

    Returns:
        (old_attachment, new_attachment, reason)
    """
    if len(attachments) < 2:
        return attachments[0], attachments[0], "single_file"

    a, b = attachments[0], attachments[1]

    # 1. Парсинг явных указаний из запроса
    query_lower = query.lower()

    # Паттерны: "[старый] filename", "старая версия: filename", "старый файл filename"
    old_patterns = [
        r'\[стар\w*\]\s*(\S+)',
        r'стар\w+\s+(?:версия|файл|документ)[:\s]+(\S+)',
        r'(\S+)\s*[-—]\s*стар\w+',
    ]
    new_patterns = [
        r'\[нов\w*\]\s*(\S+)',
        r'нов\w+\s+(?:версия|файл|документ)[:\s]+(\S+)',
        r'(\S+)\s*[-—]\s*нов\w+',
    ]

    for pattern in old_patterns:
        match = re.search(pattern, query_lower)
        if match:
            ref = match.group(1)
            # Определяем, какой файл соответствует
            if ref in a.name.lower():
                return a, b, "query_explicit"
            elif ref in b.name.lower():
                return b, a, "query_explicit"

    for pattern in new_patterns:
        match = re.search(pattern, query_lower)
        if match:
            ref = match.group(1)
            if ref in a.name.lower():
                return b, a, "query_explicit"
            elif ref in b.name.lower():
                return a, b, "query_explicit"

    # 2. Эвристика по именам файлов
    def _version_score(name: str) -> float:
        """Возвращает числовую оценку 'новизны' файла по имени. Выше = новее."""
        name_lower = name.lower()
        score = 0.0

        # v1/v2/v3 паттерны
        v_match = re.search(r'v(\d+)', name_lower)
        if v_match:
            score += int(v_match.group(1)) * 100

        # _ред1, _ред2, _red1, _red2
        red_match = re.search(r'(?:ред|red)[._-]?(\d+)', name_lower)
        if red_match:
            score += int(red_match.group(1)) * 100

        # old/new, стар/нов в имени
        if any(kw in name_lower for kw in ['old', 'стар', '_стар', 'prev', 'before']):
            score -= 500
        if any(kw in name_lower for kw in ['new', 'нов', '_нов', 'current', 'after']):
            score += 500

        # Даты в имени: YYYY-MM-DD, YYYYMMDD, DD.MM.YYYY
        date_patterns = [
            (r'(\d{4})[-_](\d{2})[-_](\d{2})', lambda m: int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))),
            (r'(\d{8})', lambda m: int(m.group(1))),
            (r'(\d{2})\.(\d{2})\.(\d{4})', lambda m: int(m.group(3)) * 10000 + int(m.group(2)) * 100 + int(m.group(1))),
        ]
        for dp, extractor in date_patterns:
            dm = re.search(dp, name_lower)
            if dm:
                score += extractor(dm) * 0.001
                break

        # Timestamp в имени (unix epoch, например _1688590800)
        ts_match = re.search(r'_(\d{10})', name)
        if ts_match:
            score += int(ts_match.group(1)) * 0.001

        # Номер закона/документа (больше номер = более поздний)
        num_match = re.search(r'[HНh](\d{5,})', name)
        if num_match:
            score += int(num_match.group(1)) * 0.01

        return score

    score_a = _version_score(a.name)
    score_b = _version_score(b.name)

    if abs(score_a - score_b) > 0.001:
        if score_a < score_b:
            return a, b, "filename_heuristic"
        else:
            return b, a, "filename_heuristic"

    # 3. Парсинг дат из содержимого документов (если тексты доступны в сессии)
    if session_docs:
        text_a = session_docs.get(a.name, {}).get("text", "")
        text_b = session_docs.get(b.name, {}).get("text", "")
        if text_a and text_b:
            date_a = _extract_document_date(text_a)
            date_b = _extract_document_date(text_b)
            if date_a is not None and date_b is not None and date_a != date_b:
                if date_a < date_b:
                    return a, b, "content_date"
                else:
                    return b, a, "content_date"

    # 4. Fallback: mtime
    try:
        mtime_a = os.path.getmtime(a.path)
        mtime_b = os.path.getmtime(b.path)
        if mtime_a < mtime_b:
            return a, b, "mtime"
        elif mtime_b < mtime_a:
            return b, a, "mtime"
    except OSError:
        pass

    # Совсем fallback: порядок как пришли
    return a, b, "default"

# === Сессионный менеджер документов (Задача 4) ===

# Ключевые слова, при которых документы из сессии включаются в контекст LLM
DOC_CONTEXT_KEYWORDS = [
    "из документа", "в файле", "на основе", "что написано", "найди в",
    "по документу", "согласно", "в тексте", "цитат", "упомянут",
    "из файла", "содержится", "указано в", "прочитай"
]
MAX_CONTEXT_CHARS = 16000  # ~4000 токенов — безопасно для Qwen-14B (8192 ctx)
MAX_HISTORY_MESSAGES = 10  # Максимум сообщений в multi-turn промпте


def _build_multiturn_prompt(messages: List[Dict[str, Any]], system_suffix: str = "") -> str:
    """
    Строит ChatML промпт из массива messages[] (OpenAI format).
    Обрезает до MAX_HISTORY_MESSAGES последних сообщений.

    Args:
        messages: Список сообщений [{role, content}, ...]
        system_suffix: Дополнительный текст для system-промпта (например, контекст документов)

    Returns:
        ChatML-промпт для Qwen
    """
    system_msg = "Ты помощник Agent Navigator. Помогай пользователю."
    if system_suffix:
        system_msg += f"\n\n{system_suffix}"

    # Отделяем system от остальных сообщений
    conversation = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Multimodal: извлекаем текст
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        if role == "system":
            system_msg = content + (f"\n\n{system_suffix}" if system_suffix else "")
        else:
            conversation.append({"role": role, "content": content})

    # Обрезаем до MAX_HISTORY_MESSAGES (сохраняем последние)
    if len(conversation) > MAX_HISTORY_MESSAGES:
        conversation = conversation[-MAX_HISTORY_MESSAGES:]

    # Собираем ChatML
    prompt = f"<|im_start|>system\n{system_msg}<|im_end|>\n"
    for msg in conversation:
        role = msg["role"]
        content = msg["content"]
        prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"

    # Если последнее сообщение не от assistant, добавляем начало ответа
    if not conversation or conversation[-1]["role"] != "assistant":
        prompt += "<|im_start|>assistant\n"

    return prompt

def _get_or_create_session(session_id: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Возвращает существующую или создаёт новую сессию."""
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]
    sid = session_id or str(uuid.uuid4())
    sessions[sid] = {
        "documents": {},  # filename -> {text, chunks, path, loaded_at}
        "created_at": time.time(),
        "last_activity": time.time(),
    }
    return sid, sessions[sid]

def _should_include_doc_context(query: str) -> bool:
    """Проверяет, содержит ли запрос ключевые слова для подключения контекста документов."""
    query_lower = query.lower()
    return any(kw in query_lower for kw in DOC_CONTEXT_KEYWORDS)

def _build_doc_context(session: Dict[str, Any], max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Строит контекст из документов сессии, обрезая до лимита."""
    if not session["documents"]:
        return ""
    parts = []
    total = 0
    for fname, doc_info in session["documents"].items():
        text = doc_info.get("text", "")
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining] + "..."
        parts.append(f"--- Документ: {fname} ---\n{text}")
        total += len(text)
    return "\n\n".join(parts)

# === Workflow Dispatcher ===

async def run_workflow_stream(session_id: str, query: str, attachments: List[FileAttachment], target_model: str = "agent-navigator", messages: Optional[List[Dict[str, Any]]] = None):
    """Запускает нужный LangGraph или прямой инференс в зависимости от контекста и модели."""

    # Получаем/создаём сессию
    sid, session = _get_or_create_session(session_id)
    session["last_activity"] = time.time()

    # Загружаем новые файлы в сессию
    new_files_loaded = []
    if attachments:
        MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
        for att in attachments:
            if att.name not in session["documents"]:
                try:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": att.path})
                        if resp.status_code == 200:
                            text = resp.json().get("text", "")
                            session["documents"][att.name] = {
                                "text": text,
                                "path": att.path,
                                "loaded_at": time.time(),
                            }
                            new_files_loaded.append(att.name)
                except Exception as e:
                    print(f"[Session] Failed to preload {att.name}: {e}")

    if new_files_loaded:
        names_str = ", ".join(new_files_loaded)
        yield {"type": "thought", "content": f"Загружено в сессию: {names_str}. Спросите меня о содержимом."}

    # 1. Прямой доступ к модели (Direct Model Access)
    if target_model != "agent-navigator":
        yield {"type": "thought", "content": f"Использую прямую модель: {target_model}"}

        from services.model_manager.ums_client import ums_client
        prompt = f"<|im_start|>user\n{query}\n<|im_end|>\n<|im_start|>assistant\n"

        try:
            response = ums_client.infer(target_model, {"prompt": prompt, "temperature": 0.7})
            content = _extract_content(response)
            yield {"type": "final_answer", "content": content}
        except Exception as e:
            yield {"type": "error", "content": f"Ошибка инференса {target_model}: {str(e)}"}
        return

    # 2. Логика АГЕНТА (agent-navigator) - Soft ReAct Router
    query_lower = query.lower()
    is_compare_intent = any(kw in query_lower for kw in ["сравни", "различия", "изменения"])
    is_equipment_intent = any(kw in query_lower for kw in ["смета", "оборудование", "тз", "закупка"])
    # T3.1: Считаем только НОВЫЕ файлы (не загруженные ранее в сессию)
    new_file_count = sum(1 for a in attachments if a.name not in session["documents"])
    file_count = len(attachments)

    workflow = None
    initial_state = {}
    task_name = "Чат"

    # Сценарий A: 2+ НОВЫХ файла и интент -> Графы (Compare или Equipment)
    if new_file_count >= 2 and (is_compare_intent or is_equipment_intent):
        active_attachments = attachments[:2]

        if is_compare_intent:
            # Определяем порядок документов (старый/новый)
            old_doc, new_doc, reason = determine_document_order(active_attachments, query, session.get("documents"))
            yield {"type": "thought", "content": f"Старая версия: {os.path.basename(old_doc.name)} | Новая версия: {os.path.basename(new_doc.name)} (метод: {reason})"}

            workflow = create_compare_graph()
            initial_state = {
                "input_1": old_doc.path, "input_2": new_doc.path,
                "chunks_old": [], "chunks_new": [], "matches": [],
                "analysis_results": [], "final_report": "", "errors": [],
                "session_id": sid,
            }
            task_name = "Сравнение документов"
        elif is_equipment_intent:
            workflow = create_equipment_graph()
            tz_path = next((a.path for a in active_attachments if "тз" in a.name.lower() or "req" in a.name.lower()), active_attachments[0].path)
            smeta_path = next((a.path for a in active_attachments if "смет" in a.name.lower() or "offer" in a.name.lower()), active_attachments[1].path)
            initial_state = {
                "input_tz": tz_path, "input_smeta": smeta_path,
                "requirements": [], "offers": [], "matches": [],
                "final_report": "", "errors": []
            }
            task_name = "Анализ оборудования"

    # Сценарий B: 1 файл и интент "Смета" -> Извлечение позиций через Map-Reduce
    elif file_count == 1 and is_equipment_intent:
        yield {"type": "thought", "content": "Вижу запрос на анализ сметы, но файл один. Извлекаю данные..."}
        file_path = attachments[0].path
        MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": file_path})
                if resp.status_code == 200:
                    text = resp.json().get("text", "")
                    chunk_size = 10000
                    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
                    all_items = []
                    for i, chunk in enumerate(chunks):
                        yield {"type": "thought", "content": f"Извлекаю позиции ({i+1}/{len(chunks)})..."}
                        prompt = f"<|im_start|>system\nТы аналитик смет. Извлеки товары в формате JSON: [{{'item': '...', 'price': '...', 'qty': '...'}}]. Отвечай только JSON.\n<|im_end|>\n<|im_start|>user\n{chunk}\n<|im_end|>\n<|im_start|>assistant\n["
                        from services.model_manager.ums_client import ums_client
                        response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.1})
                        all_items.append(_extract_content(response))
                    
                    final_data = "".join(all_items)
                    yield {"type": "final_answer", "content": f"**Данные извлечены из сметы:**\n\n{final_data}"}
                    return
            except Exception as e:
                yield {"type": "error", "content": f"Ошибка анализа сметы: {e}"}
                return

    # Сценарий C: 1 файл и нет интента -> Глубокий анализ (Map-Reduce) / Vision
    elif file_count == 1:
        file_path = attachments[0].path
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ['.jpg', '.jpeg', '.png', '.webp']:
            yield {"type": "final_answer", "content": f"Обнаружено изображение {os.path.basename(file_path)}. Vision-анализ в разработке."}
            return

        yield {"type": "thought", "content": "Запускаю глубокий анализ документа..."}
        MCP_DOCUMENT_SERVER_URL = os.getenv("MCP_DOCUMENT_SERVER_URL", "http://localhost:8001")
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                resp = await client.post(f"{MCP_DOCUMENT_SERVER_URL}/load_document", json={"path": file_path})
                text = resp.json().get("text", "")
                if not text:
                    yield {"type": "final_answer", "content": "Не удалось извлечь текст из документа."}; return
                
                chunk_size = 8000
                chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
                
                if len(chunks) == 1:
                    prompt = f"<|im_start|>system\nТы аналитик. Сделай подробное резюме документа.\n<|im_end|>\n<|im_start|>user\n{text}\n<|im_end|>\n<|im_start|>assistant\n"
                    from services.model_manager.ums_client import ums_client
                    response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.3})
                    yield {"type": "final_answer", "content": _extract_content(response)}
                    return

                summaries = []
                for i, chunk in enumerate(chunks):
                    yield {"type": "thought", "content": f"Анализ части {i+1}/{len(chunks)}..."}
                    prompt = f"<|im_start|>system\nСделай подробный конспект фрагмента документа. Сохрани важные детали.\n<|im_end|>\n<|im_start|>user\n{chunk}\n<|im_end|>\n<|im_start|>assistant\n"
                    from services.model_manager.ums_client import ums_client
                    summaries.append(_extract_content(ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.3})))
                
                yield {"type": "thought", "content": "Финализация общего отчета..."}
                combined = "\n\n".join(summaries)
                prompt = f"<|im_start|>system\nОбъедини эти конспекты в единый связный подробный отчет.\n<|im_end|>\n<|im_start|>user\n{combined}\n<|im_end|>\n<|im_start|>assistant\n"
                from services.model_manager.ums_client import ums_client
                yield {"type": "final_answer", "content": _extract_content(ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.3}))}
                return
            except Exception as e:
                yield {"type": "error", "content": f"Ошибка: {str(e)}"}; return

    # Запуск графа (Compare/Equipment) или Чат
    if workflow:
        yield {"type": "thought", "content": f"Запускаю процесс: {task_name}..."}
        try:
            async for event in workflow.astream(initial_state):
                for node_name, output in event.items():
                    if "final_report" in output and output["final_report"]:
                        paragraphs = output["final_report"].split("\n\n")
                        for para in paragraphs:
                            if para.strip():
                                yield {"type": "final_answer", "content": para + "\n\n"}
                    elif "errors" in output and output["errors"]:
                        yield {"type": "error", "content": "; ".join(output["errors"])}
                    else:
                        yield {"type": "thought", "content": f"Завершено: {node_name}"}
        except Exception as e: yield {"type": "error", "content": f"Ошибка графа: {e}"}
    else:
        # Прямой чат с on-demand контекстом документов (Задача 4)
        from services.model_manager.ums_client import ums_client

        # T3.2: Multi-turn промпт из messages[]
        doc_context_suffix = ""
        if session["documents"] and _should_include_doc_context(query):
            doc_context = _build_doc_context(session)
            doc_context_suffix = f"Контекст документов:\n{doc_context}"

        if messages and len(messages) > 1:
            # Multi-turn: строим ChatML из всей истории
            prompt = _build_multiturn_prompt(messages, system_suffix=doc_context_suffix)
        else:
            # Single-turn fallback
            system_msg = "Ты помощник Agent Navigator. Помогай пользователю."
            if doc_context_suffix:
                system_msg += f"\n\n{doc_context_suffix}"
            prompt = f"<|im_start|>system\n{system_msg}<|im_end|>\n<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n"

        # Задача 3: стриминг для прямого чата
        try:
            async for token in ums_client.async_infer_stream("qwen-14b-llm", {"prompt": prompt, "temperature": 0.7}):
                yield {"type": "final_answer", "content": token}
        except Exception as e:
            # Fallback на обычный инференс если стриминг не поддерживается
            try:
                response = ums_client.infer("qwen-14b-llm", {"prompt": prompt, "temperature": 0.7})
                yield {"type": "final_answer", "content": _extract_content(response)}
            except Exception as e2:
                yield {"type": "error", "content": f"Ошибка генерации: {e2}"}

def _extract_content(response):
    if isinstance(response, dict):
        if "content" in response: return response["content"]
        if "choices" in response and len(response["choices"]) > 0:
            choice = response["choices"][0]
            if isinstance(choice, dict):
                return choice.get("text", "") or choice.get("message", {}).get("content", "")
    return str(response).strip()

# === OpenAI Compatible API ===

@app.get("/v1/models")
def list_models():
    """Возвращает динамический список доступных GGUF моделей."""
    models = [{"id": "agent-navigator", "object": "model", "created": int(time.time()), "owned_by": "agent-navigator-pro"}]
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, 'models', 'gguf')
        if os.path.exists(models_dir):
            for root, dirs, files in os.walk(models_dir):
                for file in files:
                    if file.endswith(".gguf"):
                        mid = os.path.splitext(file)[0]
                        models.append({"id": mid, "object": "model", "created": int(os.path.getctime(os.path.join(root, file))), "owned_by": "local-fs"})
    except Exception as e: print(f"Error scanning models: {e}")
    return {"object": "list", "data": models}

@app.post("/v1/chat/completions")
async def openai_completions(request: Request):
    print(">>> NEW REQUEST (V2.3 - Dedup+Order+Stream) <<<")
    data = await request.json()
    messages = data.get("messages", [])
    target_model = data.get("model", "agent-navigator")

    # Парсинг текста запроса (нужно для дедупликации с хешем файлов)
    user_query = ""
    if messages:
        content = messages[-1].get("content", "")
        if isinstance(content, str): user_query = content
        elif isinstance(content, list):
            user_query = " ".join([p.get("text", "") for p in content if p.get("type") == "text"])

    # Поиск файлов в папке uploads
    found_files = []
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        WEBUI_PATH = os.path.join(base_dir, 'open_webui_uploads')
        if os.path.exists(WEBUI_PATH):
            recent = []
            now = time.time()
            for root, _, files in os.walk(WEBUI_PATH):
                for f in files:
                    p = os.path.join(root, f)
                    if now - os.path.getmtime(p) < 600 and not f.startswith("Report_"):
                        recent.append(p)
            recent.sort(key=os.path.getmtime, reverse=True)
            for rf in recent:
                found_files.append(FileAttachment(name=os.path.basename(rf), path=rf, size=os.path.getsize(rf), type="webui_upload"))
    except Exception as e: print(f"Error scanning uploads: {e}")

    # Парсинг путей из текста
    try:
        path_pattern = r'(?:/[\w\-. /]+|\bbackend/[\w\-. /]+)'
        matches = re.findall(path_pattern, user_query)
        for m in matches:
            cp = m.strip()
            if cp and os.path.exists(cp) and not any(f.path == cp for f in found_files):
                found_files.insert(0, FileAttachment(name=os.path.basename(cp), path=cp, size=0, type="manual"))
    except: pass

    # Дедупликация (Задача 2): хеш файлов + текст запроса + модель, окно DEDUP_WINDOW_SEC
    files_hash = _compute_files_hash([f.path for f in found_files]) if found_files else "no_files"
    dedup_key = f"{target_model}:{files_hash}:{hashlib.md5(user_query.encode()).hexdigest()[:16]}"
    now_ts = time.time()
    if dedup_key in _active_workflows and now_ts - _active_workflows[dedup_key] < DEDUP_WINDOW_SEC:
        print(f"[DEDUP] Duplicate request blocked (window={DEDUP_WINDOW_SEC}s): {dedup_key[:80]}")
        async def _empty():
            yield "data: [DONE]\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")
    _active_workflows[dedup_key] = now_ts
    # Чистим устаревшие ключи (старше 10 мин)
    for k in list(_active_workflows.keys()):
        if now_ts - _active_workflows[k] > 600:
            del _active_workflows[k]

    # Генерируем session_id на основе набора файлов (для привязки workflow к запросу)
    session_id = f"s_{files_hash[:12]}" if found_files else f"s_{uuid.uuid4().hex[:12]}"

    async def generate():
        try:
            if target_model == "agent-navigator":
                yield "data: " + json.dumps({"choices": [{"delta": {"role": "assistant", "content": "Анализирую ваш запрос...\n\n"}}]}) + "\n\n"

            async for step in run_workflow_stream(session_id, user_query, found_files, target_model, messages=messages):
                txt = step.get("content", "")
                if step["type"] == "thought" and target_model == "agent-navigator":
                    txt = f"*[Thought: {txt}]*\n"

                if txt:
                    chunk = {"choices": [{"delta": {"content": txt}, "finish_reason": None}]}
                    yield "data: " + json.dumps(chunk) + "\n\n"

            yield "data: [DONE]\n\n"
        finally:
            # Снимаем блокировку дедупликации при любом исходе:
            # нормальное завершение, исключение, отключение клиента
            _active_workflows.pop(dedup_key, None)

    return StreamingResponse(generate(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
