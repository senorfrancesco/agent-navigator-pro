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
import copy
import logging
import os
import re
import sys
import time
import shutil
import httpx
from typing import Dict, List, Optional, Any, TypedDict, Literal

# Добавляем пути (оставляем для обратной совместимости, но используем абсолютные)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import chainlit as cl
except ImportError:
    raise ImportError("chainlit not installed. Run: pip install chainlit")

from services.model_manager.ums_client import ums_client
from orchestrator.shared.http_client import get_shared_client

logger = logging.getLogger("chainlit_app")

INTENT_LOW_MARGIN_THRESHOLD = 0.12
INTENT_LOW_CONFIDENCE_THRESHOLD = 0.55
ROUTE_CHOICE_TIMEOUT_S = 90
_DOC_REQUIRED_INTENTS = {"document_question", "compare_documents", "equipment_analysis"}
_RESTORE_CONFIRMATION_PHRASES = [
    "подтверждаю",
    "да, продолжай",
    "продолжай",
    "всё равно",
    "все равно",
    "запусти",
]
_CONTEXT_RECOVERY_MESSAGE = (
    "Контекст документов восстановлен не полностью. "
    "Чтобы вернуть документные сценарии, загрузите документы заново "
    "(кнопка скрепки) и дождитесь сообщения о готовности RAG-индекса."
)

_COMPARE_QUERY_KEYWORDS = [
    "сравни", "сравнение", "различия", "отличия", "что изменилось", "покажи разницу",
]
_EQUIPMENT_QUERY_KEYWORDS = [
    "тз", "техническое задание", "коммерческое предложение", "кп", "смета",
    "оборудование", "подходит", "что подходит", "что нам подходит",
    "соответствует", "соответствие", "подходит ли",
]
_DOC_QUESTION_KEYWORDS = [
    "что", "какой", "какая", "какие", "сколько", "найди", "покажи", "указано",
    "написано", "содержится", "есть ли",
]
_DOC_QUESTION_UPLOAD_REQUEST_PHRASES = [
    "предоставьте тексты",
    "предоставьте текст",
    "пришлите текст",
    "загрузите тексты",
    "загрузите текст",
    "нужно увидеть тексты",
    "мне нужно увидеть тексты",
    "предоставьте содержание",
    "нужно содержание",
]

DOC_QA_MIN_CHUNKS_SIMPLE = int(os.getenv("DOC_QA_MIN_CHUNKS_SIMPLE", "1"))
DOC_QA_MIN_CHUNKS_MULTIHOP = int(os.getenv("DOC_QA_MIN_CHUNKS_MULTIHOP", "2"))
DOC_QA_MIN_RAW_SCORE_SIMPLE = float(os.getenv("DOC_QA_MIN_RAW_SCORE_SIMPLE", "0.01"))
DOC_QA_MIN_ZSCORE_CORRECTIVE = float(os.getenv("DOC_QA_MIN_ZSCORE_CORRECTIVE", "-0.5"))


class SourceRef(TypedDict):
    source_id: int
    document_id: str
    chunk_id: int
    char_span: Dict[str, Optional[int]]
    page: Optional[int]
    quote: str
    raw_score: float
    normalized_score: float
    grade: Optional[str]
    z_score: Optional[float]


class DocQuestionResponse(TypedDict):
    answer_text: str
    sources: List[SourceRef]
    answer_mode: Literal["grounded_answer", "insufficient_evidence"]
    fallback_type: Literal["none", "citation_validation_failed", "insufficient_evidence"]
    fallback_reason: Optional[str]
    confidence: float
    confidence_label: Literal["high", "medium", "low"]
    confidence_method: Literal["heuristic_v1"]
    confidence_version: Literal["1"]


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


def _get_pending_route_choice() -> Optional[Dict[str, Any]]:
    return cl.user_session.get("pending_route_choice")


def _set_pending_route_choice(data: Optional[Dict[str, Any]]) -> None:
    cl.user_session.set("pending_route_choice", data)


def _get_context_state() -> Dict[str, Any]:
    state = cl.user_session.get("context_state")
    if state is None:
        state = {
            "status": "empty",
            "reason": "missing_docs",
            "has_valid_docs": False,
            "index_ready": False,
            "expected_docs": 0,
            "valid_docs": 0,
            "source": "runtime",
            "message": _CONTEXT_RECOVERY_MESSAGE,
            "confirmed_partial": False,
        }
        cl.user_session.set("context_state", state)
    return state


def _set_context_state(state: Dict[str, Any]) -> None:
    cl.user_session.set("context_state", state)


def _build_context_state(
    session_docs: Dict[str, Any],
    rag_pipeline: Optional[Any],
    *,
    source: str = "runtime",
    expected_docs: Optional[int] = None,
    confirmed_partial: Optional[bool] = None,
) -> Dict[str, Any]:
    valid_docs = sum(
        1
        for doc_info in session_docs.values()
        if isinstance(doc_info, dict)
        and doc_info.get("text")
        and str(doc_info.get("text", "")).strip()
        and doc_info.get("path")
    )
    has_valid_docs = valid_docs > 0
    index_ready = bool(rag_pipeline and getattr(rag_pipeline, "_indexed", False))
    expected = expected_docs if expected_docs is not None else valid_docs

    reason = "ready"
    status = "ready"
    if expected_docs is not None and expected_docs > valid_docs:
        reason = "resume_partial"
        status = "degraded"
        logger.warning(
            "Context degraded: resume_partial (expected_docs=%s, valid_docs=%s)",
            expected_docs,
            valid_docs,
        )
    elif not has_valid_docs:
        reason = "missing_docs"
        status = "empty"
        logger.warning("Context degraded: missing_docs")
    elif not index_ready:
        reason = "index_not_ready"
        status = "degraded"
        logger.warning("Context degraded: index_not_ready")

    return {
        "status": status,
        "reason": reason,
        "has_valid_docs": has_valid_docs,
        "index_ready": index_ready,
        "expected_docs": expected,
        "valid_docs": valid_docs,
        "source": source,
        "message": _CONTEXT_RECOVERY_MESSAGE,
        "confirmed_partial": bool(confirmed_partial),
    }


def _is_explicit_restore_confirmation(query: str) -> bool:
    query_lower = query.lower().strip()
    return any(phrase in query_lower for phrase in _RESTORE_CONFIRMATION_PHRASES)


# === Intent Detection ===

def _detect_intent(
    query: str,
    file_count: int = 0,
    has_session_docs: bool = False,
    context_state: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Семантическая классификация интентов (Semantic Router).
    TD-10: Жесткие списки ключевых слов удалены. Вся маршрутизация идет
    через EmbeddingIntentClassifier (поиск ближайших соседей в векторном пространстве).
    """
    query_lower = query.lower()
    if context_state is None:
        context_state = {
            "reason": "ready",
            "confirmed_partial": True,
            "has_valid_docs": has_session_docs or file_count > 0,
            "index_ready": True,
        }

    if context_state.get("reason") == "resume_partial" and not context_state.get("confirmed_partial"):
        return "general_chat"

    has_ready_context = context_state.get("has_valid_docs", False) and context_state.get("index_ready", False)
    has_session_docs = has_session_docs and has_ready_context

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

        if needs_rag and not has_ready_context:
            return "general_chat"

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


def _has_any_keyword(query_lower: str, keywords: List[str]) -> bool:
    return any(kw in query_lower for kw in keywords)


def _is_low_confidence(classifier_result: Optional[Dict[str, Any]]) -> bool:
    if not classifier_result:
        return True
    return (
        classifier_result.get("confidence", 0.0) < INTENT_LOW_CONFIDENCE_THRESHOLD
        or classifier_result.get("margin", 0.0) < INTENT_LOW_MARGIN_THRESHOLD
    )


def _get_last_session_docs(session_docs: Dict[str, Any], count: int = 2) -> List[Dict[str, Any]]:
    items = list(session_docs.items())[-count:]
    return [{"name": name, **doc_info} for name, doc_info in items]


def _build_route_choice_prompt(recommended_route: str, mode: str) -> str:
    if mode == "tz_vs_smeta":
        base = "Похоже, у вас ТЗ и коммерческое предложение."
    else:
        base = "Я вижу два загруженных документа."

    if recommended_route == "equipment_analysis":
        return (
            f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
            "1. Проверить, что из КП подходит под ТЗ\n"
            "2. Просто сравнить документы\n"
            "3. Задать вопрос по содержимому"
        )
    if recommended_route == "compare_documents":
        return (
            f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
            "1. Сравнить документы\n"
            "2. Проверить соответствие ТЗ и КП\n"
            "3. Задать вопрос по содержимому"
        )
    return (
        f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
        "1. Задать вопрос по документам\n"
        "2. Сравнить документы\n"
        "3. Проверить соответствие ТЗ и КП"
    )


def _build_route_choice_state(
    query: str,
    recommended_route: str,
    new_files: List[Dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    if recommended_route == "equipment_analysis":
        choices = {"1": "equipment_analysis", "2": "compare_documents", "3": "document_question"}
    elif recommended_route == "compare_documents":
        choices = {"1": "compare_documents", "2": "equipment_analysis", "3": "document_question"}
    else:
        choices = {"1": "document_question", "2": "compare_documents", "3": "equipment_analysis"}
    return {
        "query": query,
        "new_files": copy.deepcopy(new_files),
        "choices": choices,
        "recommended_route": recommended_route,
        "mode": mode,
        "expires_at": time.time() + ROUTE_CHOICE_TIMEOUT_S,
    }


def _resolve_pending_route_choice(query: str, pending_choice: Optional[Dict[str, Any]]) -> Optional[str]:
    if not pending_choice:
        return None
    if pending_choice.get("expires_at", 0) < time.time():
        return None
    choice = query.strip().lower()
    if choice in ("отмена", "cancel"):
        return "cancel"
    return pending_choice.get("choices", {}).get(choice)


def _get_classifier_result(query: str) -> Optional[Dict[str, Any]]:
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
    return classifier_result


def _get_intent_decision(
    query: str,
    file_count: int = 0,
    has_session_docs: bool = False,
    session_docs: Optional[Dict[str, Any]] = None,
    classifier_result: Optional[Dict[str, Any]] = None,
    context_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    query_lower = query.lower().strip()
    if context_state is None:
        context_state = {
            "reason": "ready",
            "confirmed_partial": True,
            "has_valid_docs": has_session_docs or bool(session_docs),
            "index_ready": True,
            "message": _CONTEXT_RECOVERY_MESSAGE,
        }
    session_docs = session_docs if session_docs is not None else (_get_session_docs() if has_session_docs else {})
    has_session_docs = has_session_docs or bool(session_docs)
    context_ready = context_state.get("has_valid_docs", False) and context_state.get("index_ready", False)
    total_docs = max(file_count, len(session_docs))
    two_docs = total_docs >= 2
    single_doc = total_docs == 1
    classifier_result = classifier_result if classifier_result is not None else _get_classifier_result(query)

    compare_signal = _has_any_keyword(query_lower, _COMPARE_QUERY_KEYWORDS)
    equipment_signal = _has_any_keyword(query_lower, _EQUIPMENT_QUERY_KEYWORDS)
    doc_question_signal = _has_any_keyword(query_lower, _DOC_QUESTION_KEYWORDS)

    if context_state.get("reason") == "resume_partial" and not context_state.get("confirmed_partial"):
        return {
            "intent": "general_chat",
            "requires_choice": False,
            "confidence": 0.0,
            "margin": 0.0,
            "reason": "resume_partial_requires_confirmation",
            "context_message": context_state.get("message", _CONTEXT_RECOVERY_MESSAGE),
        }


    mode = None
    if two_docs and session_docs:
        docs = _get_last_session_docs(session_docs, count=2)
        if len(docs) == 2:
            mode = _detect_equipment_mode(
                docs[0]["name"],
                docs[1]["name"],
                query,
                text_1=docs[0].get("text", "")[:1000],
                text_2=docs[1].get("text", "")[:1000],
            )

    if two_docs and mode == "tz_vs_smeta" and equipment_signal:
        if classifier_result and classifier_result.get("intent") == "equipment_analysis" and not _is_low_confidence(classifier_result):
            return {
                "intent": "equipment_analysis",
                "requires_choice": False,
                "confidence": classifier_result.get("confidence", 0.0),
                "margin": classifier_result.get("margin", 0.0),
                "reason": "tz_vs_smeta_high_confidence",
            }
        return {
            "intent": "equipment_analysis",
            "requires_choice": True,
            "recommended_route": "equipment_analysis",
            "confidence": (classifier_result or {}).get("confidence", 0.0),
            "margin": (classifier_result or {}).get("margin", 0.0),
            "reason": "tz_vs_smeta_ambiguous",
            "mode": mode or "tz_vs_smeta",
        }

    if classifier_result:
        intent = classifier_result["intent"]
        needs_rag = classifier_result["needs_rag"]
        low_confidence = _is_low_confidence(classifier_result)

        if intent == "document_analysis":
            if not single_doc:
                if two_docs:
                    intent = "document_question"
                else:
                    intent = "general_chat"
            elif not (file_count >= 1 or has_session_docs):
                intent = "general_chat"

        if two_docs and low_confidence and compare_signal and not equipment_signal:
            return {
                "intent": "compare_documents",
                "requires_choice": True,
                "recommended_route": "compare_documents",
                "confidence": classifier_result.get("confidence", 0.0),
                "margin": classifier_result.get("margin", 0.0),
                "reason": "compare_ambiguous",
                "mode": mode or "unknown",
            }

        if needs_rag and not context_ready:
            return {
                "intent": "general_chat",
                "requires_choice": False,
                "confidence": classifier_result.get("confidence", 0.0),
                "margin": classifier_result.get("margin", 0.0),
                "reason": f"context_invalid_{context_state.get('reason', 'unknown')}",
                "context_message": context_state.get("message", _CONTEXT_RECOVERY_MESSAGE),
            }

        if has_session_docs and needs_rag and intent not in ("compare_documents", "equipment_analysis", "document_analysis"):
            intent = "document_question"

        return {
            "intent": intent,
            "requires_choice": False,
            "confidence": classifier_result.get("confidence", 0.0),
            "margin": classifier_result.get("margin", 0.0),
            "reason": "semantic_router",
        }

    return {
        "intent": _detect_intent(
            query,
            file_count=file_count,
            has_session_docs=has_session_docs,
            context_state=context_state,
        ),
        "requires_choice": False,
        "confidence": 0.0,
        "margin": 0.0,
        "reason": "minimal_fallback",
    }


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
    msg.content = await _infer_assistant_text(prompt)

    await msg.send()
    history.append({"role": "assistant", "content": msg.content})


async def _infer_assistant_text(prompt: str, temperature: float = 0.7) -> str:
    try:
        response = await asyncio.to_thread(
            ums_client.infer,
            "qwen-14b-llm",
            {"prompt": prompt, "temperature": temperature},
        )
        return response.get("choices", [{}])[0].get("text", str(response))
    except Exception:
        try:
            logger.warning("Direct chat infer failed, retrying sync inference", exc_info=True)
            response = ums_client.infer(
                "qwen-14b-llm", {"prompt": prompt, "temperature": temperature}
            )
            return response.get("choices", [{}])[0].get("text", str(response))
        except Exception as e:
            return f"Ошибка генерации: {e}"


def _needs_doc_question_regen(answer_text: str, has_session_docs: bool) -> bool:
    if not has_session_docs:
        return False
    text = (answer_text or "").lower()
    if any(phrase in text for phrase in _DOC_QUESTION_UPLOAD_REQUEST_PHRASES):
        return True
    # Более общий guard: модель уходит в "уточните/пришлите содержание", хотя контекст уже передан.
    if "уточните" in text and ("содержан" in text or "текст" in text or "требован" in text):
        return True
    if "не могу предоставить точный ответ" in text and ("уточните" in text or "предостав" in text):
        return True
    return False


def _normalize_quote(text: str, max_len: int = 320) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= max_len:
        return flat
    return flat[:max_len].rsplit(" ", 1)[0] + "..."


def _normalize_score_minmax(raw: float, min_score: float, max_score: float) -> float:
    if max_score <= min_score:
        return 1.0 if raw == max_score else 0.8
    return max(0.0, min(1.0, (raw - min_score) / (max_score - min_score)))


def _build_sources_from_rag_result(rag_result: Any, rag_pipeline: Any, max_sources: int = 5) -> List[SourceRef]:
    chunks = list(getattr(rag_result, "chunks", []) or [])[:max_sources]
    if not chunks:
        return []

    all_raw_scores = [float(c.score) for c in chunks]
    min_score = min(all_raw_scores)
    max_score = max(all_raw_scores)
    all_chunk_meta = list(getattr(rag_pipeline, "_chunks", []) or [])

    sources: List[SourceRef] = []
    for idx, c in enumerate(chunks, 1):
        chunk_id = int(getattr(c, "index", -1))
        chunk_obj = all_chunk_meta[chunk_id] if 0 <= chunk_id < len(all_chunk_meta) else None
        meta = getattr(chunk_obj, "metadata", {}) if chunk_obj is not None else {}
        start_char = getattr(chunk_obj, "start_char", None) if chunk_obj is not None else None
        end_char = getattr(chunk_obj, "end_char", None) if chunk_obj is not None else None
        sources.append(
            {
                "source_id": idx,
                "document_id": str(meta.get("doc_name", f"doc_{chunk_id}" if chunk_id >= 0 else "unknown")),
                "chunk_id": chunk_id,
                "char_span": {"start_char": start_char, "end_char": end_char},
                "page": None,
                "quote": _normalize_quote(getattr(c, "text", "")),
                "raw_score": float(getattr(c, "score", 0.0)),
                "normalized_score": _normalize_score_minmax(float(getattr(c, "score", 0.0)), min_score, max_score),
                "grade": getattr(c, "metadata", {}).get("grade"),
                "z_score": getattr(c, "metadata", {}).get("z_score"),
            }
        )
    return sources


def _extract_citation_ids(answer_text: str) -> List[int]:
    return [int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", answer_text or "")]


def _citations_are_valid(answer_text: str, source_count: int) -> bool:
    cited = _extract_citation_ids(answer_text)
    if not cited:
        return False
    return all(1 <= cid <= source_count for cid in cited)


def _is_multihop_query(query: str) -> bool:
    query_lower = (query or "").lower()
    markers = ["сравни", "сопостав", "что подходит", "какие отличия", "и ", " vs ", " между "]
    return any(m in query_lower for m in markers)


def _has_sufficient_evidence(
    sources: List[SourceRef],
    mode: str,
    query: str,
    citations_valid: bool,
) -> bool:
    if not citations_valid:
        return False
    min_chunks = DOC_QA_MIN_CHUNKS_MULTIHOP if _is_multihop_query(query) else DOC_QA_MIN_CHUNKS_SIMPLE
    if len(sources) < min_chunks:
        return False

    top = sources[0]
    raw_top = float(top.get("raw_score", 0.0))
    z_top = top.get("z_score")
    grade_top = (top.get("grade") or "").lower()
    mode = (mode or "simple").lower()

    if mode in ("corrective", "agentic"):
        if z_top is not None:
            return float(z_top) >= DOC_QA_MIN_ZSCORE_CORRECTIVE
        return grade_top in ("excellent", "good") or raw_top >= DOC_QA_MIN_RAW_SCORE_SIMPLE
    return raw_top >= DOC_QA_MIN_RAW_SCORE_SIMPLE


def _confidence_label(value: float) -> Literal["high", "medium", "low"]:
    if value >= 0.75:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def _compute_confidence_v1(
    sources: List[SourceRef],
    cited_ids: List[int],
    answer_mode: Literal["grounded_answer", "insufficient_evidence"],
) -> tuple[float, Literal["high", "medium", "low"]]:
    cited_sources = [s for s in sources if s["source_id"] in cited_ids] if cited_ids else []
    if not cited_sources:
        base = 0.2
    else:
        avg_raw = sum(float(s.get("raw_score", 0.0)) for s in cited_sources) / len(cited_sources)
        avg_norm = sum(float(s.get("normalized_score", 0.0)) for s in cited_sources) / len(cited_sources)
        good_bonus = 0.08 if any((s.get("grade") or "").lower() in ("good", "excellent") for s in cited_sources) else 0.0
        base = max(0.0, min(1.0, 0.25 + 0.35 * avg_norm + 0.30 * avg_raw + good_bonus))

    if answer_mode == "insufficient_evidence":
        base = min(base, 0.35)
    return base, _confidence_label(base)


def _build_doc_question_deterministic_fallback(
    query: str,
    sources: List[SourceRef],
    fallback_type: Literal["citation_validation_failed", "insufficient_evidence"],
) -> DocQuestionResponse:
    top_sources = sources[:2]
    if top_sources:
        lines = [
            f"По запросу «{query}» в найденных фрагментах есть только следующие подтверждённые данные:",
        ]
        for s in top_sources:
            lines.append(f"- [{s['source_id']}] {s['quote']}")
        lines.append("Данных недостаточно для точного вывода без дополнительных подтверждений.")
        answer_text = "\n".join(lines)
    else:
        answer_text = (
            f"По запросу «{query}» в текущем контексте загруженных документов "
            "недостаточно подтверждённых данных для точного вывода."
        )

    confidence, label = _compute_confidence_v1(sources, [], "insufficient_evidence")
    return {
        "answer_text": answer_text,
        "sources": sources,
        "answer_mode": "insufficient_evidence",
        "fallback_type": fallback_type,
        "fallback_reason": "Недостаточно подтверждённых данных или невалидный citation-ответ модели.",
        "confidence": confidence,
        "confidence_label": label,
        "confidence_method": "heuristic_v1",
        "confidence_version": "1",
    }


def _build_doc_question_prompt_with_sources(query: str, history: List, sources: List[SourceRef]) -> str:
    lines = []
    for s in sources:
        span = s["char_span"]
        lines.append(
            f"[{s['source_id']}] doc={s['document_id']} chunk={s['chunk_id']} "
            f"span=({span.get('start_char')},{span.get('end_char')}) quote={s['quote']}"
        )
    catalog = "\n".join(lines)
    system_msg = (
        "Ты помощник Agent Navigator.\n"
        "Отвечай только на основе CATALOG OF SOURCES.\n"
        "Каждое фактическое утверждение помечай ссылками [n] из каталога.\n"
        "Запрещено использовать ссылки вне диапазона каталога.\n"
        "Если данных недостаточно, прямо скажи это и укажи ограничения, не выдумывай.\n"
        "Не проси повторно загрузить документы/тексты.\n\n"
        f"CATALOG OF SOURCES:\n{catalog}"
    )
    return _build_prompt(query, history, system_msg)


def _render_doc_question_markdown(resp: DocQuestionResponse) -> str:
    lines = [resp["answer_text"].strip(), "", "### Источники"]
    if resp["sources"]:
        for s in resp["sources"]:
            score_pct = int(round(float(s["normalized_score"]) * 100))
            lines.append(
                f"- [{s['source_id']}] `{s['document_id']}` chunk={s['chunk_id']} "
                f"span=({s['char_span'].get('start_char')},{s['char_span'].get('end_char')}) "
                f"relevance={score_pct}% raw={s['raw_score']:.4f}"
            )
            lines.append(f"  Цитата: {s['quote']}")
    else:
        lines.append("- Источники не найдены.")
    lines.extend(
        [
            "",
            "### Надёжность",
            f"- confidence: {resp['confidence']:.2f} ({resp['confidence_label']})",
            f"- method: {resp['confidence_method']} v{resp['confidence_version']}",
            f"- mode: {resp['answer_mode']}",
            f"- fallback: {resp['fallback_type']}",
        ]
    )
    if resp.get("fallback_reason"):
        lines.append(f"- reason: {resp['fallback_reason']}")
    return "\n".join(lines)


# === Chainlit Handlers ===

async def _init_classifier():
    """Фоновая инициализация EmbeddingIntentClassifier до загрузки файлов."""
    try:
        from orchestrator.rag.classifier import EmbeddingIntentClassifier
        from services.model_manager.ums_client import create_ums_embed_fn

        retries = int(os.getenv("CHAINLIT_CLASSIFIER_PREINIT_RETRIES", "6"))
        delay_s = float(os.getenv("CHAINLIT_CLASSIFIER_PREINIT_DELAY_S", "2.0"))
        embed_fn = None

        for attempt in range(1, retries + 1):
            embed_fn = await asyncio.to_thread(create_ums_embed_fn)
            if embed_fn:
                break
            if attempt < retries:
                logger.info(
                    f"Classifier pre-init: UMS unavailable, retrying ({attempt}/{retries})"
                )
                await asyncio.sleep(delay_s)

        if not embed_fn:
            logger.info("Classifier pre-init skipped: UMS unavailable after retries")
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
    _set_context_state(_build_context_state({}, None, source="chat_start"))

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
    _set_context_state(_build_context_state({}, None, source="resume"))
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
                    try:
                        await asyncio.to_thread(rag.index_documents, all_texts, doc_names=all_names)
                        cl.user_session.set("rag_pipeline", rag)
                        context_state = _build_context_state(
                            session_docs,
                            rag,
                            source="resume",
                            expected_docs=len(unique_files),
                        )
                        _set_context_state(context_state)
                        step.output = f"Восстановлено {len(names)} документов, RAG готов."
                    except Exception as e:
                        logger.warning(f"Resume RAG indexing failed: {e}")
                        context_state = _build_context_state(
                            session_docs,
                            None,
                            source="resume",
                            expected_docs=len(unique_files),
                        )
                        _set_context_state(context_state)
                        step.output = "Документы восстановлены частично. Индекс пока не готов."
                else:
                    context_state = _build_context_state(
                        session_docs,
                        None,
                        source="resume",
                        expected_docs=len(unique_files),
                    )
                    _set_context_state(context_state)
                    step.output = "Не удалось восстановить текст документов."


@cl.on_message
async def on_message(message: cl.Message):
    query = message.content
    history = _get_session_history()
    session_docs = _get_session_docs()
    context_state = _get_context_state()

    if context_state.get("reason") == "resume_partial" and _is_explicit_restore_confirmation(query):
        context_state = {**context_state, "confirmed_partial": True}
        _set_context_state(context_state)

    pending_choice = _get_pending_route_choice()
    if pending_choice:
        selected_route = _resolve_pending_route_choice(query, pending_choice)
        if pending_choice.get("expires_at", 0) < time.time():
            _set_pending_route_choice(None)
        elif selected_route:
            _set_pending_route_choice(None)
            if selected_route == "cancel":
                await cl.Message(content="Выбор отменён.").send()
            else:
                await _execute_intent(
                    selected_route,
                    pending_choice["query"],
                    pending_choice.get("new_files", []),
                    session_docs,
                    history,
                    context_state=context_state,
                )
            history.append({"role": "user", "content": query})
            return

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

    rag = cl.user_session.get("rag_pipeline")
    context_state = _build_context_state(session_docs, rag, source="runtime", confirmed_partial=context_state.get("confirmed_partial"))
    _set_context_state(context_state)

    decision = _get_intent_decision(
        query,
        file_count=len(new_files),
        has_session_docs=bool(session_docs),
        session_docs=session_docs,
        context_state=context_state,
    )
    if decision.get("requires_choice"):
        recommended_route = decision["recommended_route"]
        prompt_text = _build_route_choice_prompt(recommended_route, decision.get("mode", "unknown"))
        state = _build_route_choice_state(query, recommended_route, new_files, decision.get("mode", "unknown"))
        response = await cl.AskActionMessage(
            content=prompt_text,
            actions=[
                cl.Action(name="route_choice", payload={"route": state["choices"]["1"]}, label="1"),
                cl.Action(name="route_choice", payload={"route": state["choices"]["2"]}, label="2"),
                cl.Action(name="route_choice", payload={"route": state["choices"]["3"]}, label="3"),
                cl.Action(name="route_choice", payload={"route": "cancel"}, label="Отмена"),
            ],
            timeout=ROUTE_CHOICE_TIMEOUT_S,
            raise_on_timeout=False,
        ).send()
        if response and response.get("payload", {}).get("route"):
            await _execute_intent(response["payload"]["route"], query, new_files, session_docs, history, context_state=context_state)
        else:
            _set_pending_route_choice(state)
            await cl.Message(
                content=(
                    "Не дождался выбора. Ответьте сообщением: "
                    "1 — первый вариант, 2 — второй, 3 — третий, или 'отмена'."
                )
            ).send()
            history.append({"role": "user", "content": query})
            return
    else:
        await _execute_intent(decision["intent"], query, new_files, session_docs, history, context_state=context_state)

    history.append({"role": "user", "content": query})


async def _execute_intent(
    intent: str,
    query: str,
    new_files: List,
    session_docs: Dict,
    history: List,
    context_state: Optional[Dict[str, Any]] = None,
):
    context_state = context_state or _get_context_state()
    if intent in _DOC_REQUIRED_INTENTS:
        context_ready = context_state.get("has_valid_docs", False) and context_state.get("index_ready", False)
        partial_resume_without_confirmation = (
            context_state.get("reason") == "resume_partial" and not context_state.get("confirmed_partial")
        )
        if not context_ready or partial_resume_without_confirmation:
            await cl.Message(content=context_state.get("message", _CONTEXT_RECOVERY_MESSAGE)).send()
            await _handle_chat(query, session_docs, history)
            return

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
    """Вопрос по документам с inline citations, sources-блоком и evidence-policy."""

    rag = cl.user_session.get("rag_pipeline")
    rag_result = None
    rag_meta: Dict[str, Any] = {}
    rag_mode = "simple"

    if rag and rag._indexed:
        async with cl.Step(name="Поиск по документам", type="retrieval") as step:
            try:
                rag_result = await asyncio.to_thread(rag.retrieve, query)
                rag_meta = rag_result.metadata or {}
                rag_mode = str(rag_meta.get("mode", "simple"))
                intent = rag_result.intent or {}
                found_chunks = len(rag_result.chunks or [])
                step.output = (
                    f"Найдено {found_chunks} релевантных фрагментов "
                    f"(mode: {rag_meta.get('mode', '?')}, "
                    f"intent: {intent.get('intent', '?')}, "
                    f"confidence: {intent.get('confidence', 0):.2f})"
                )
            except Exception as e:
                logger.warning(f"RAG retrieve failed, falling back to naive: {e}")
                rag_result = None

    if rag_result is None:
        await cl.Message(
            content=(
                "По текущему запросу не удалось получить проверяемые источники из RAG. "
                "Уточните формулировку или вопрос к конкретной позиции."
            )
        ).send()
        return

    sources = _build_sources_from_rag_result(rag_result, rag)
    if not sources:
        fallback = _build_doc_question_deterministic_fallback(
            query=query,
            sources=[],
            fallback_type="insufficient_evidence",
        )
        rendered = _render_doc_question_markdown(fallback)
        msg = cl.Message(content=rendered)
        await msg.send()
        history.append({"role": "assistant", "content": rendered})
        return

    prompt = _build_doc_question_prompt_with_sources(query, history, sources)
    response_text = await _infer_assistant_text(prompt, temperature=0.3)
    citations_valid = _citations_are_valid(response_text, source_count=len(sources))

    if not citations_valid or _needs_doc_question_regen(response_text, has_session_docs=bool(session_docs)):
        strict_prompt = _build_doc_question_prompt_with_sources(
            query,
            history,
            sources,
        ) + "\n\nЖЕСТКОЕ ПРАВИЛО: обязательно используй только валидные ссылки [n] из каталога."
        response_text = await _infer_assistant_text(strict_prompt, temperature=0.2)
        citations_valid = _citations_are_valid(response_text, source_count=len(sources))

    if not citations_valid:
        fallback = _build_doc_question_deterministic_fallback(
            query=query,
            sources=sources,
            fallback_type="citation_validation_failed",
        )
        rendered = _render_doc_question_markdown(fallback)
        logger.info(
            "DocQuestion fallback: type=%s mode=%s sources=%s",
            fallback["fallback_type"],
            rag_mode,
            len(sources),
        )
        msg = cl.Message(content=rendered)
        await msg.send()
        history.append({"role": "assistant", "content": rendered})
        return

    cited_ids = _extract_citation_ids(response_text)
    has_evidence = _has_sufficient_evidence(
        sources=sources,
        mode=rag_mode,
        query=query,
        citations_valid=True,
    )
    if not has_evidence:
        payload = _build_doc_question_deterministic_fallback(
            query=query,
            sources=sources,
            fallback_type="insufficient_evidence",
        )
    else:
        confidence, label = _compute_confidence_v1(sources, cited_ids, "grounded_answer")
        payload: DocQuestionResponse = {
            "answer_text": response_text,
            "sources": sources,
            "answer_mode": "grounded_answer",
            "fallback_type": "none",
            "fallback_reason": None,
            "confidence": confidence,
            "confidence_label": label,
            "confidence_method": "heuristic_v1",
            "confidence_version": "1",
        }

    logger.info(
        "DocQuestion result: mode=%s chunks=%s citations=%s answer_mode=%s fallback=%s top_raw=%.4f conf=%.2f",
        rag_mode,
        len(sources),
        len(cited_ids),
        payload["answer_mode"],
        payload["fallback_type"],
        float(sources[0]["raw_score"]) if sources else 0.0,
        payload["confidence"],
    )
    rendered = _render_doc_question_markdown(payload)
    msg = cl.Message(content=rendered)
    await msg.send()
    history.append({"role": "assistant", "content": rendered})


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
