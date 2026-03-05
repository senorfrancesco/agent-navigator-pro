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
import uuid
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

_COMPARE_QUERY_KEYWORDS = [
    "сравни", "сравнение", "различия", "отличия", "изменения",
    "что изменилось", "что поменялось", "покажи разницу",
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
_SUMMARY_QUERY_KEYWORDS = [
    "о чем документ",
    "о чём документ",
    "о чем документы",
    "о чём документы",
    "о чем эти документы",
    "о чём эти документы",
    "суть документа",
    "суть документов",
    "кратко по документ",
    "суммариз",
    "сводк",
    "проанализируй эти документы",
    "проанализируй документы",
    "анализируй эти документы",
    "анализируй документы",
    "анализ документов",
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
_SOCIAL_ONLY_RE = re.compile(
    r"^(?:"
    r"спасибо(?:\s+большое)?|благодарю|спс|"
    r"ок(?:ей)?|понятно|ясно|понял(?:а)?|хорошо|"
    r"привет|здравствуй(?:те)?|добрый день|добрый вечер|доброе утро|"
    r"hi|hello|thanks|thank you"
    r")$",
    re.IGNORECASE,
)

DOC_QA_MIN_CHUNKS_SIMPLE = int(os.getenv("DOC_QA_MIN_CHUNKS_SIMPLE", "1"))
DOC_QA_MIN_CHUNKS_MULTIHOP = int(os.getenv("DOC_QA_MIN_CHUNKS_MULTIHOP", "2"))
DOC_QA_MIN_RAW_SCORE_SIMPLE = float(os.getenv("DOC_QA_MIN_RAW_SCORE_SIMPLE", "0.01"))
DOC_QA_MIN_ZSCORE_CORRECTIVE = float(os.getenv("DOC_QA_MIN_ZSCORE_CORRECTIVE", "-0.5"))
RAG_INDEX_CACHE_MAX = int(os.getenv("RAG_INDEX_CACHE_MAX", "8"))
RAG_INDEX_CACHE_TTL_S = int(os.getenv("RAG_INDEX_CACHE_TTL_S", "1800"))


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

def _ensure_session_state() -> None:
    if cl.user_session.get("history") is None:
        cl.user_session.set("history", [])
    if cl.user_session.get("documents_by_id") is None:
        cl.user_session.set("documents_by_id", {})
    if cl.user_session.get("documents_by_name") is None:
        cl.user_session.set("documents_by_name", {})
    if cl.user_session.get("active_doc_ids") is None:
        cl.user_session.set("active_doc_ids", [])
    if cl.user_session.get("rag_pipeline_cache") is None:
        cl.user_session.set("rag_pipeline_cache", {})
    if cl.user_session.get("documents") is None:
        cl.user_session.set("documents", {})

    # Legacy migration: {name: {text, path}} -> documents_by_id/documents_by_name
    docs_by_id = cl.user_session.get("documents_by_id") or {}
    legacy_docs = cl.user_session.get("documents") or {}
    if legacy_docs and not docs_by_id:
        migrated_by_id: Dict[str, Any] = {}
        migrated_by_name: Dict[str, List[str]] = {}
        ts_base = time.time()
        for idx, (name, info) in enumerate(legacy_docs.items(), start=1):
            doc_id = f"legacy-{idx}"
            migrated_by_id[doc_id] = {
                "document_id": doc_id,
                "display_name": name,
                "version": 1,
                "path": info.get("path", ""),
                "text": info.get("text", ""),
                "uploaded_at": ts_base + idx / 1000.0,
                "source_message_id": None,
            }
            migrated_by_name.setdefault(name, []).append(doc_id)
        cl.user_session.set("documents_by_id", migrated_by_id)
        cl.user_session.set("documents_by_name", migrated_by_name)
        cl.user_session.set("active_doc_ids", list(migrated_by_id.keys())[-2:])


def _get_documents_by_id() -> Dict[str, Any]:
    _ensure_session_state()
    return cl.user_session.get("documents_by_id") or {}


def _get_documents_by_name() -> Dict[str, List[str]]:
    _ensure_session_state()
    docs_by_id = _get_documents_by_id()
    rebuilt: Dict[str, List[str]] = {}
    docs_sorted = sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0)))
    for doc in docs_sorted:
        name = str(doc.get("display_name", "")).strip()
        doc_id = str(doc.get("document_id", "")).strip()
        if not name or not doc_id:
            continue
        rebuilt.setdefault(name, []).append(doc_id)
    cl.user_session.set("documents_by_name", rebuilt)
    return rebuilt


def _doc_label(display_name: str, version: int) -> str:
    return f"{display_name} (v{version})" if int(version) > 1 else display_name


def _sync_legacy_documents_cache() -> Dict[str, Any]:
    docs_by_id = _get_documents_by_id()
    latest_by_name: Dict[str, Any] = {}
    for doc in sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0))):
        name = str(doc.get("display_name", "")).strip()
        if not name:
            continue
        existing = latest_by_name.get(name)
        if existing is None or int(doc.get("version", 1)) >= int(existing.get("version", 1)):
            latest_by_name[name] = doc

    legacy: Dict[str, Any] = {}
    for doc in sorted(latest_by_name.values(), key=lambda d: float(d.get("uploaded_at", 0.0))):
        legacy[doc["display_name"]] = {
            "text": doc.get("text", ""),
            "path": doc.get("path", ""),
            "document_id": doc.get("document_id"),
            "version": int(doc.get("version", 1)),
        }
    cl.user_session.set("documents", legacy)
    return legacy


def _get_session_docs() -> Dict[str, Any]:
    # Backward-compatible view: latest version per display_name.
    _ensure_session_state()
    return _sync_legacy_documents_cache()


def _get_active_doc_ids() -> List[str]:
    _ensure_session_state()
    active_ids = list(cl.user_session.get("active_doc_ids") or [])
    docs_by_id = _get_documents_by_id()
    active_ids = [doc_id for doc_id in active_ids if doc_id in docs_by_id]
    if active_ids:
        cl.user_session.set("active_doc_ids", active_ids)
        return active_ids

    if not docs_by_id:
        return []

    docs_sorted = sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0)))
    default_ids = [str(docs_sorted[-1]["document_id"])]
    if len(docs_sorted) >= 2:
        default_ids = [str(docs_sorted[-2]["document_id"]), str(docs_sorted[-1]["document_id"])]
    cl.user_session.set("active_doc_ids", default_ids)
    return default_ids


def _set_active_doc_ids(doc_ids: List[str]) -> List[str]:
    docs_by_id = _get_documents_by_id()
    unique: List[str] = []
    seen = set()
    for doc_id in doc_ids:
        if doc_id in docs_by_id and doc_id not in seen:
            seen.add(doc_id)
            unique.append(doc_id)
    cl.user_session.set("active_doc_ids", unique)
    # При смене активного набора старый pending-choice становится невалидным.
    _set_pending_route_choice(None)
    return unique


def _get_active_docs() -> List[Dict[str, Any]]:
    docs_by_id = _get_documents_by_id()
    active_ids = _get_active_doc_ids()
    return [docs_by_id[doc_id] for doc_id in active_ids if doc_id in docs_by_id]


def _get_all_docs() -> List[Dict[str, Any]]:
    docs_by_id = _get_documents_by_id()
    return sorted(docs_by_id.values(), key=lambda d: float(d.get("uploaded_at", 0.0)))


def _get_report_docs() -> List[Dict[str, Any]]:
    return [
        d
        for d in _get_all_docs()
        if str(d.get("display_name", "")).lower().startswith("report_")
    ]


def _get_active_session_docs() -> Dict[str, Any]:
    active_docs = _get_active_docs()
    result: Dict[str, Any] = {}
    for doc in active_docs:
        key = str(doc.get("display_name", "")).strip()
        if key in result:
            key = _doc_label(key, int(doc.get("version", 1)))
        result[key] = {
            "text": doc.get("text", ""),
            "path": doc.get("path", ""),
            "document_id": doc.get("document_id", ""),
            "display_name": doc.get("display_name", key),
            "version": int(doc.get("version", 1)),
        }
    return result


def _active_set_status_line() -> str:
    active_docs = _get_active_docs()
    if not active_docs:
        return "Активный набор: пусто"
    labels = [_doc_label(str(d["display_name"]), int(d.get("version", 1))) for d in active_docs]
    return f"Активный набор: {', '.join(labels)} ({len(labels)} docs)"


def _next_doc_version(display_name: str) -> int:
    docs_by_name = _get_documents_by_name()
    docs_by_id = _get_documents_by_id()
    versions = [
        int(docs_by_id[doc_id].get("version", 1))
        for doc_id in docs_by_name.get(display_name, [])
        if doc_id in docs_by_id
    ]
    return (max(versions) + 1) if versions else 1


def _register_loaded_document(
    *,
    display_name: str,
    path: str,
    text: str,
    source_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    docs_by_id = _get_documents_by_id()
    docs_by_name = _get_documents_by_name()
    doc_id = str(uuid.uuid4())
    version = _next_doc_version(display_name)
    record = {
        "document_id": doc_id,
        "display_name": display_name,
        "version": version,
        "path": path,
        "text": text,
        "uploaded_at": time.time(),
        "source_message_id": source_message_id,
    }
    docs_by_id[doc_id] = record
    docs_by_name.setdefault(display_name, []).append(doc_id)
    cl.user_session.set("documents_by_id", docs_by_id)
    cl.user_session.set("documents_by_name", docs_by_name)
    _sync_legacy_documents_cache()
    return record


def _get_session_history() -> List[Dict[str, str]]:
    _ensure_session_state()
    history = cl.user_session.get("history")
    if history is None:
        history = []
        cl.user_session.set("history", history)
    return history


def _get_pending_route_choice() -> Optional[Dict[str, Any]]:
    _ensure_session_state()
    return cl.user_session.get("pending_route_choice")


def _set_pending_route_choice(data: Optional[Dict[str, Any]]) -> None:
    cl.user_session.set("pending_route_choice", data)


# === Intent Detection ===

def _detect_intent(query: str, file_count: int = 0, has_session_docs: bool = False) -> str:
    """
    Семантическая классификация интентов (Semantic Router).
    TD-10: Жесткие списки ключевых слов удалены. Вся маршрутизация идет
    через EmbeddingIntentClassifier (поиск ближайших соседей в векторном пространстве).
    """
    query_lower = query.lower()
    active_docs_count = len(_get_active_doc_ids()) if has_session_docs else 0
    if has_session_docs and active_docs_count == 0:
        active_docs_count = len(_get_session_docs())

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
            has_file = file_count >= 1 or (has_session_docs and active_docs_count >= 1)
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
    
    single_file = file_count == 1 or (has_session_docs and active_docs_count == 1)
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


def _is_social_query(query: str) -> bool:
    normalized = re.sub(r"[\s\.,!?;:()\"'«»…-]+", " ", (query or "").strip().lower()).strip()
    if not normalized:
        return False
    return bool(_SOCIAL_ONLY_RE.match(normalized))


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
            "3. Задать вопрос по содержимому\n"
            "4. Сделать общую сводку по документам"
        )
    if recommended_route == "compare_documents":
        return (
            f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
            "1. Сравнить документы\n"
            "2. Проверить соответствие ТЗ и КП\n"
            "3. Задать вопрос по содержимому\n"
            "4. Сделать общую сводку по документам"
        )
    return (
        f"{base} Запрос неоднозначный. Что вы хотите сделать?\n\n"
        "1. Задать вопрос по документам\n"
        "2. Сравнить документы\n"
        "3. Проверить соответствие ТЗ и КП\n"
        "4. Сделать общую сводку по документам"
    )


def _build_route_choice_state(
    query: str,
    recommended_route: str,
    new_files: List[Dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    if recommended_route == "equipment_analysis":
        choices = {
            "1": "equipment_analysis",
            "2": "compare_documents",
            "3": "document_question",
            "4": "documents_summary",
        }
    elif recommended_route == "compare_documents":
        choices = {
            "1": "compare_documents",
            "2": "equipment_analysis",
            "3": "document_question",
            "4": "documents_summary",
        }
    else:
        choices = {
            "1": "document_question",
            "2": "compare_documents",
            "3": "equipment_analysis",
            "4": "documents_summary",
        }
    return {
        "route_choice_id": str(uuid.uuid4())[:8],
        "origin_trace_id": cl.user_session.get("request_trace_id"),
        "query": query,
        "new_files": copy.deepcopy(new_files),
        "choices": choices,
        "recommended_route": recommended_route,
        "mode": mode,
        "active_doc_ids": list(_get_active_doc_ids()),
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


def _is_docs_summary_query(query: str) -> bool:
    query_lower = (query or "").lower()
    return any(kw in query_lower for kw in _SUMMARY_QUERY_KEYWORDS)


def _is_report_query(query: str) -> bool:
    q = (query or "").lower()
    return ("отчет" in q) or ("отчёт" in q) or ("report_" in q) or ("по отчету" in q) or ("по отчёту" in q)


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
) -> Dict[str, Any]:
    query_lower = query.lower().strip()
    session_docs = session_docs if session_docs is not None else (_get_session_docs() if has_session_docs else {})
    has_session_docs = has_session_docs or bool(session_docs)
    social_query = _is_social_query(query)
    total_docs = max(file_count, len(session_docs))
    has_any_docs = total_docs > 0
    has_any_docs = total_docs > 0
    two_docs = total_docs >= 2
    single_doc = total_docs == 1
    classifier_result = classifier_result if classifier_result is not None else _get_classifier_result(query)

    compare_signal = _has_any_keyword(query_lower, _COMPARE_QUERY_KEYWORDS)
    equipment_signal = _has_any_keyword(query_lower, _EQUIPMENT_QUERY_KEYWORDS)
    doc_question_signal = _has_any_keyword(query_lower, _DOC_QUESTION_KEYWORDS)
    summary_signal = _is_docs_summary_query(query)

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

    # Guardrail: короткие social/greeting реплики не должны сбрасывать контекст
    # и не должны запускать workflow/ask-upload при непустом active scope.
    if has_session_docs and social_query:
        return {
            "intent": "greeting",
            "requires_choice": False,
            "confidence": (classifier_result or {}).get("confidence", 1.0),
            "margin": (classifier_result or {}).get("margin", 1.0),
            "reason": "social_guard",
        }

    if has_any_docs and summary_signal and not compare_signal and not equipment_signal:
        return {
            "intent": "documents_summary",
            "requires_choice": False,
            "confidence": (classifier_result or {}).get("confidence", 0.0),
            "margin": (classifier_result or {}).get("margin", 0.0),
            "reason": "summary_query",
        }

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

        # Без загруженных документов не уходим в doc_question RAG.
        if not has_any_docs and intent == "document_question":
            intent = "general_chat"
            needs_rag = False

        # Guardrail: без загруженных документов не отправляем запрос в doc-question RAG,
        # даже если классификатор ошибочно считает, что нужен retrieval.
        if not has_any_docs and intent == "document_question":
            intent = "general_chat"
            needs_rag = False

        if intent == "document_analysis":
            if not single_doc:
                if two_docs:
                    display_mode = mode if (mode == "tz_vs_smeta" and equipment_signal) else "unknown"
                    return {
                        "intent": "document_analysis",
                        "requires_choice": True,
                        "recommended_route": "document_question",
                        "confidence": classifier_result.get("confidence", 0.0),
                        "margin": classifier_result.get("margin", 0.0),
                        "reason": "document_analysis_multi_doc",
                        "mode": display_mode,
                    }
                intent = "general_chat"
            elif not (file_count >= 1 or has_session_docs):
                intent = "general_chat"

        if two_docs and low_confidence:
            if mode == "tz_vs_smeta" and equipment_signal:
                recommended_route = "equipment_analysis"
            elif compare_signal:
                recommended_route = "compare_documents"
            elif doc_question_signal:
                recommended_route = "document_question"
            elif intent in ("compare_documents", "equipment_analysis", "document_question"):
                recommended_route = intent
            else:
                recommended_route = "compare_documents"
            return {
                "intent": recommended_route,
                "requires_choice": True,
                "recommended_route": recommended_route,
                "confidence": classifier_result.get("confidence", 0.0),
                "margin": classifier_result.get("margin", 0.0),
                "reason": "two_docs_low_confidence",
                "mode": mode if (mode == "tz_vs_smeta" and equipment_signal) else "unknown",
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
        "intent": _detect_intent(query, file_count=file_count, has_session_docs=has_session_docs),
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


def _normalize_doc_match_key(text: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", (text or "").lower())


def _resolve_target_doc_name(query: str, active_docs: List[Dict[str, Any]]) -> Optional[str]:
    query_norm = _normalize_doc_match_key(query)
    if not query_norm:
        return None

    best: Optional[tuple[int, str]] = None
    for doc in active_docs:
        display_name = str(doc.get("display_name", "")).strip()
        if not display_name:
            continue
        stem = os.path.splitext(display_name)[0]
        variants = {_normalize_doc_match_key(display_name), _normalize_doc_match_key(stem)}
        for variant in variants:
            if not variant:
                continue
            if variant in query_norm:
                score = len(variant)
                if best is None or score > best[0]:
                    best = (score, display_name)
    return best[1] if best else None


def _reindex_sources(sources: List[SourceRef]) -> List[SourceRef]:
    reindexed: List[SourceRef] = []
    for idx, src in enumerate(sources, start=1):
        copied = dict(src)
        copied["source_id"] = idx
        reindexed.append(copied)
    return reindexed


def _normalize_quote(text: str, max_len: int = 320) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= max_len:
        return flat
    return flat[:max_len].rsplit(" ", 1)[0] + "..."


def _normalize_score_minmax(raw: float, min_score: float, max_score: float) -> float:
    spread = max_score - min_score
    if spread <= 1e-9:
        return 0.5
    normalized = max(0.0, min(1.0, (raw - min_score) / spread))
    if spread < 0.05:
        # На малой выборке score часто очень близки; сжимаем шкалу для честного UI.
        return 0.35 + 0.30 * normalized
    return normalized


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
    fallback_reason: Optional[str] = None,
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
        "fallback_reason": fallback_reason or "Недостаточно подтверждённых данных или невалидный citation-ответ модели.",
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
        "Не добавляй отдельные разделы 'Источники' и 'Надёжность' — "
        "система добавляет их автоматически.\n\n"
        f"CATALOG OF SOURCES:\n{catalog}"
    )
    return _build_prompt(query, history, system_msg)


def _strip_model_source_sections(answer_text: str) -> str:
    text = (answer_text or "").strip()
    if not text:
        return text

    patterns = [
        r"(?im)^\s*#{0,3}\s*источники\s*$",
        r"(?im)^\s*источники\s*$",
        r"(?im)^\s*#{0,3}\s*надежность\s*$",
        r"(?im)^\s*#{0,3}\s*надёжность\s*$",
        r"(?im)^\s*надежность\s*$",
        r"(?im)^\s*надёжность\s*$",
    ]

    cut_pos = len(text)
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            cut_pos = min(cut_pos, match.start())

    cleaned = text[:cut_pos].rstrip()
    return cleaned if cleaned else text


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


def _extract_saved_report_filename(report_text: str) -> Optional[str]:
    match = re.search(r"\*\*Отчет (?:сохранен|уже сохранен):\*\*\s*`([^`]+)`", report_text or "")
    if not match:
        return None
    return match.group(1).strip()


async def _attach_and_register_report(report_text: str) -> None:
    filename = _extract_saved_report_filename(report_text)
    if not filename:
        return

    report_path = os.path.join(UPLOADS_DIR, filename)
    if not os.path.exists(report_path):
        return

    try:
        file_el = cl.File(name=filename, path=report_path, display="inline")
        await cl.Message(content=f"Файл отчёта: `{filename}`", elements=[file_el]).send()
    except Exception:
        logger.warning("Failed to attach report file element for %s", filename, exc_info=True)

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report_text_content = f.read()
        _register_loaded_document(
            display_name=filename,
            path=report_path,
            text=report_text_content,
            source_message_id=cl.user_session.get("request_trace_id"),
        )
    except Exception:
        logger.warning("Failed to register report in session docs: %s", filename, exc_info=True)


def _build_rag_index_key(doc_ids: List[str]) -> str:
    return "|".join(sorted(doc_ids))


async def _ensure_rag_index_for_active_docs(step_name: Optional[str] = None) -> bool:
    """Индексирует RAG только по active_docs. Возвращает True, если была переиндексация."""
    active_docs = _get_active_docs()
    if not active_docs:
        cl.user_session.set("rag_index_key", "")
        cl.user_session.set("rag_index_doc_ids", [])
        return False

    active_ids = [str(d["document_id"]) for d in active_docs]
    index_key = _build_rag_index_key(active_ids)
    now_ts = time.time()

    cache: Dict[str, Any] = cl.user_session.get("rag_pipeline_cache") or {}
    for key, entry in list(cache.items()):
        last_used = float(entry.get("last_used", entry.get("indexed_at", 0.0)))
        if RAG_INDEX_CACHE_TTL_S > 0 and (now_ts - last_used) > RAG_INDEX_CACHE_TTL_S:
            cache.pop(key, None)

    cached_entry = cache.get(index_key)
    if cached_entry and cached_entry.get("pipeline") is not None:
        cached_entry["last_used"] = now_ts
        cache[index_key] = cached_entry
        cl.user_session.set("rag_pipeline_cache", cache)
        cl.user_session.set("rag_pipeline", cached_entry["pipeline"])
        cl.user_session.set("rag_index_key", index_key)
        cl.user_session.set("rag_index_doc_ids", active_ids)
        return False

    rag = cl.user_session.get("rag_pipeline")
    cached_key = cl.user_session.get("rag_index_key") or ""
    if rag is not None and cached_key == index_key:
        cache[index_key] = {
            "pipeline": rag,
            "doc_ids": active_ids,
            "indexed_at": now_ts,
            "last_used": now_ts,
        }
        cl.user_session.set("rag_pipeline_cache", cache)
        return False

    from orchestrator.rag.pipeline import AdaptiveRAGPipeline
    from services.model_manager.ums_client import create_ums_embed_fn

    async def _reindex() -> tuple[AdaptiveRAGPipeline, int, str]:
        nonlocal rag
        embed_fn = create_ums_embed_fn()
        rag_mode = await _get_rag_mode()
        if rag is None:
            rag = AdaptiveRAGPipeline(embed_fn=embed_fn, rag_mode=rag_mode)
        else:
            rag.rag_mode = rag_mode
        texts = [str(d.get("text", "")) for d in active_docs if d.get("text")]
        names = [str(d.get("display_name", "")) for d in active_docs if d.get("text")]
        await asyncio.to_thread(rag.index_documents, texts, doc_names=names)
        return rag, len(texts), rag_mode

    if step_name:
        async with cl.Step(name=step_name, type="tool") as step:
            rag, indexed_count, rag_mode = await _reindex()
            search_type = "BM25+Dense (hybrid)" if rag.embed_fn else "BM25-only"
            step.output = (
                f"RAG: mode={rag_mode}, search={search_type}, indexed {indexed_count} docs\n"
                f"{_active_set_status_line()}"
            )
    else:
        rag, _, _ = await _reindex()

    cache[index_key] = {
        "pipeline": rag,
        "doc_ids": active_ids,
        "indexed_at": now_ts,
        "last_used": now_ts,
    }
    if RAG_INDEX_CACHE_MAX > 0 and len(cache) > RAG_INDEX_CACHE_MAX:
        sorted_keys = sorted(
            cache.keys(),
            key=lambda k: float((cache.get(k) or {}).get("last_used", 0.0)),
        )
        for key in sorted_keys[:-RAG_INDEX_CACHE_MAX]:
            cache.pop(key, None)

    cl.user_session.set("rag_pipeline", rag)
    cl.user_session.set("rag_pipeline_cache", cache)
    cl.user_session.set("rag_index_key", index_key)
    cl.user_session.set("rag_index_doc_ids", active_ids)
    return True


async def _ensure_rag_index_for_doc_ids(doc_ids: List[str], step_name: Optional[str] = None) -> bool:
    docs_by_id = _get_documents_by_id()
    scoped_ids = [doc_id for doc_id in doc_ids if doc_id in docs_by_id]
    if not scoped_ids:
        return False

    previous_active = _get_active_doc_ids()
    cl.user_session.set("active_doc_ids", scoped_ids)
    try:
        return await _ensure_rag_index_for_active_docs(step_name=step_name)
    finally:
        cl.user_session.set("active_doc_ids", previous_active)


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
    cl.user_session.set("documents_by_id", {})
    cl.user_session.set("documents_by_name", {})
    cl.user_session.set("active_doc_ids", [])
    cl.user_session.set("history", [])
    cl.user_session.set("active_mode", None)
    cl.user_session.set("rag_index_key", "")
    cl.user_session.set("rag_index_doc_ids", [])
    cl.user_session.set("rag_pipeline_cache", {})

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
    cl.user_session.set("documents_by_id", {})
    cl.user_session.set("documents_by_name", {})
    cl.user_session.set("active_doc_ids", [])
    cl.user_session.set("rag_index_key", "")
    cl.user_session.set("rag_index_doc_ids", [])
    cl.user_session.set("rag_pipeline_cache", {})
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
                    fnames = []
                    for raw_name in files_str.split(","):
                        name = raw_name.strip()
                        if not name:
                            continue
                        name = re.sub(r"\s+\(v\d+\)$", "", name, flags=re.IGNORECASE)
                        if name:
                            fnames.append(name)
                    found_files.extend(fnames)

    cl.user_session.set("history", history)

    # Пытаемся восстановить документы и RAG только для восстановленного активного набора
    if found_files:
        unique_files = list(set(found_files))
        files_to_load = []

        for fname in unique_files:
            # Путь в контейнере
            c_path = os.path.join(UPLOADS_DIR, fname)
            if os.path.exists(c_path):
                files_to_load.append({"name": fname, "path": c_path})

        if files_to_load:
            async with cl.Step(name="Восстановление документов", type="tool") as step:
                loaded = await _load_files(files_to_load)
                names: List[str] = []
                restored_doc_ids: List[str] = []
                for f in loaded:
                    if f.get("text"):
                        rec = _register_loaded_document(
                            display_name=f["name"],
                            path=f["path"],
                            text=f["text"],
                            source_message_id=None,
                        )
                        restored_doc_ids.append(str(rec["document_id"]))
                        names.append(_doc_label(rec["display_name"], rec["version"]))
                if restored_doc_ids:
                    _set_active_doc_ids(restored_doc_ids)
                    await _ensure_rag_index_for_active_docs()
                    step.output = f"Восстановлено {len(names)} документов.\n{_active_set_status_line()}"
                else:
                    step.output = "Не удалось восстановить текст документов."


@cl.on_message
async def on_message(message: cl.Message):
    _ensure_session_state()
    trace_id = str(uuid.uuid4())[:8]
    cl.user_session.set("request_trace_id", trace_id)

    query = message.content
    history = _get_session_history()
    active_session_docs = _get_active_session_docs()
    normalized_query = (query or "").strip().lower()

    pending_choice = _get_pending_route_choice()
    if pending_choice:
        selected_route = _resolve_pending_route_choice(query, pending_choice)
        if pending_choice.get("expires_at", 0) < time.time():
            _set_pending_route_choice(None)
        elif selected_route:
            _set_pending_route_choice(None)
            pending_doc_ids = pending_choice.get("active_doc_ids") or []
            if pending_doc_ids:
                _set_active_doc_ids(list(pending_doc_ids))
            active_session_docs = _get_active_session_docs()
            logger.info(
                "Route choice resolved trace=%s route_choice_id=%s selected=%s",
                trace_id,
                pending_choice.get("route_choice_id", "-"),
                selected_route,
            )
            if selected_route == "cancel":
                await cl.Message(content="Выбор отменён.").send()
            else:
                await _execute_intent(
                    selected_route,
                    pending_choice["query"],
                    pending_choice.get("new_files", []),
                    active_session_docs,
                    history,
                )
            history.append({"role": "user", "content": query})
            return
        else:
            # Fail-closed: pending-choice одноразовый. Любой не-choice запрос его сбрасывает,
            # чтобы следующий вопрос не воспринимался как продолжение старого меню.
            logger.info(
                "Route choice dropped trace=%s route_choice_id=%s reason=non_choice_input",
                trace_id,
                pending_choice.get("route_choice_id", "-"),
            )
            _set_pending_route_choice(None)
    elif normalized_query in ("отмена", "cancel"):
        # Явная отмена без активного pending-choice не должна запускать роутинг.
        await cl.Message(content="Нет активного выбора для отмены.").send()
        history.append({"role": "user", "content": query})
        return

    # Обработка файлов
    new_files: List[Dict[str, Any]] = []
    file_inputs: List[Dict[str, str]] = []
    if message.elements:
        seen_inputs = set()
        for element in message.elements:
            if hasattr(element, 'path') and element.path:
                fname = element.name or os.path.basename(element.path)
                key = (fname, element.path)
                if key in seen_inputs:
                    continue
                seen_inputs.add(key)
                file_inputs.append({"name": fname, "path": element.path})

        if file_inputs:
            async with cl.Step(name="Загрузка документов", type="tool") as step:
                loaded = await _load_files(file_inputs)
                names: List[str] = []
                uploaded_doc_ids: List[str] = []
                for f in loaded:
                    if f.get("text"):
                        rec = _register_loaded_document(
                            display_name=f["name"],
                            path=f["path"],
                            text=f["text"],
                            source_message_id=trace_id,
                        )
                        uploaded_doc_ids.append(str(rec["document_id"]))
                        names.append(_doc_label(rec["display_name"], rec["version"]))
                        new_files.append(
                            {
                                "id": rec["document_id"],
                                "name": rec["display_name"],
                                "path": rec["path"],
                                "version": rec["version"],
                                "name_with_version": _doc_label(rec["display_name"], rec["version"]),
                            }
                        )
                    elif f.get("error"):
                        await cl.Message(content=f"Ошибка загрузки {f['name']}: {f['error']}").send()
                if uploaded_doc_ids:
                    _set_active_doc_ids(uploaded_doc_ids)
                    step.output = f"Загружено: {', '.join(names)}\n{_active_set_status_line()}"
                else:
                    step.output = "Нет новых файлов"

            if uploaded_doc_ids:
                step_name = "Инициализация RAG" if cl.user_session.get("rag_pipeline") is None else "Переиндексация"
                await _ensure_rag_index_for_active_docs(step_name=step_name)

    active_session_docs = _get_active_session_docs()

    decision = _get_intent_decision(
        query,
        file_count=len(new_files),
        has_session_docs=bool(active_session_docs),
        session_docs=active_session_docs,
    )
    logger.info(
        "Route decision trace=%s intent=%s requires_choice=%s reason=%s conf=%.2f margin=%.3f active_docs=%s",
        trace_id,
        decision.get("intent"),
        decision.get("requires_choice"),
        decision.get("reason"),
        float(decision.get("confidence", 0.0)),
        float(decision.get("margin", 0.0)),
        len(_get_active_doc_ids()),
    )

    if decision.get("requires_choice"):
        recommended_route = decision["recommended_route"]
        prompt_text = _build_route_choice_prompt(recommended_route, decision.get("mode", "unknown"))
        state = _build_route_choice_state(query, recommended_route, new_files, decision.get("mode", "unknown"))
        logger.info(
            "Route choice required trace=%s route_choice_id=%s recommended=%s mode=%s",
            trace_id,
            state.get("route_choice_id"),
            recommended_route,
            decision.get("mode", "unknown"),
        )
        response = await cl.AskActionMessage(
            content=prompt_text,
            actions=[
                cl.Action(name="route_choice", payload={"route": state["choices"]["1"]}, label="1"),
                cl.Action(name="route_choice", payload={"route": state["choices"]["2"]}, label="2"),
                cl.Action(name="route_choice", payload={"route": state["choices"]["3"]}, label="3"),
                cl.Action(name="route_choice", payload={"route": state["choices"]["4"]}, label="4"),
                cl.Action(name="route_choice", payload={"route": "cancel"}, label="Отмена"),
            ],
            timeout=ROUTE_CHOICE_TIMEOUT_S,
            raise_on_timeout=False,
        ).send()
        if response and response.get("payload", {}).get("route"):
            chosen_route = response["payload"]["route"]
            _set_pending_route_choice(None)
            state_doc_ids = state.get("active_doc_ids") or []
            if state_doc_ids:
                _set_active_doc_ids(list(state_doc_ids))
            logger.info(
                "Route choice action trace=%s route_choice_id=%s selected=%s",
                trace_id,
                state.get("route_choice_id", "-"),
                chosen_route,
            )
            if chosen_route == "cancel":
                await cl.Message(content="Выбор отменён.").send()
            else:
                await _execute_intent(chosen_route, query, new_files, _get_active_session_docs(), history)
        else:
            _set_pending_route_choice(state)
            await cl.Message(
                content=(
                    "Не дождался выбора. Ответьте сообщением: "
                    "1 — первый вариант, 2 — второй, 3 — третий, 4 — сводка, или 'отмена'.\n"
                    f"{_active_set_status_line()}"
                )
            ).send()
            history.append({"role": "user", "content": query})
            return
    else:
        await _execute_intent(decision["intent"], query, new_files, active_session_docs, history)

    history.append({"role": "user", "content": query})


async def _execute_intent(intent: str, query: str, new_files: List, session_docs: Dict, history: List):
    social_query = _is_social_query(query)
    if intent == "document_question" and not session_docs:
        intent = "general_chat"
    if intent == "compare_documents":
        cl.user_session.set("active_mode", "compare")
        await _handle_compare(query, new_files, session_docs)
    elif intent == "equipment_analysis":
        cl.user_session.set("active_mode", "equipment")
        await _handle_equipment(query, new_files, session_docs)
    elif intent == "document_analysis":
        cl.user_session.set("active_mode", "doc_analysis")
        await _handle_document_analysis(query, new_files, session_docs)
    elif intent == "document_question":
        cl.user_session.set("active_mode", "doc_qa")
        await _handle_doc_question(query, session_docs, history)
    elif intent == "documents_summary":
        cl.user_session.set("active_mode", "doc_summary")
        await _handle_documents_summary(query, history)
    else:
        # Для social-реплик при активных документах сохраняем текущий mode.
        if not (social_query and bool(_get_active_doc_ids())):
            cl.user_session.set("active_mode", "chat")
        await _handle_chat(query, session_docs, history, social_query=social_query)


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
        file_names = list(session_docs.keys())
        if len(file_names) < 2:
            await cl.Message(content="Нужно минимум 2 документа для сравнения.").send()
            return
        if len(file_names) > 2:
            labels = "\n".join(f"- {name}" for name in file_names)
            await cl.Message(
                content=(
                    "В активном наборе больше 2 документов. Уточните пару для сравнения "
                    "или загрузите только нужные файлы в одном сообщении.\n"
                    f"{_active_set_status_line()}\n{labels}"
                )
            ).send()
            return
        files = [{"name": n, "path": session_docs[n]["path"]} for n in file_names]

    if len(files) < 2 or any(not f.get("path") for f in files):
        await cl.Message(content="Недостаточно валидных файлов для сравнения. Загрузите 2 документа заново.").send()
        return

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
                await _attach_and_register_report(report)
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
        file_names = list(session_docs.keys())
        if len(file_names) < 2:
            await cl.Message(content="Нужно минимум 2 документа (ТЗ и смета).").send()
            return
        if len(file_names) > 2:
            labels = "\n".join(f"- {name}" for name in file_names)
            await cl.Message(
                content=(
                    "В активном наборе больше 2 документов. Уточните пару ТЗ/КП "
                    "или загрузите только нужные файлы в одном сообщении.\n"
                    f"{_active_set_status_line()}\n{labels}"
                )
            ).send()
            return
        files = [{"name": n, "path": session_docs[n]["path"]} for n in file_names]

    if len(files) < 2 or any(not f.get("path") for f in files):
        await cl.Message(content="Недостаточно валидных файлов для анализа оборудования. Загрузите ТЗ и КП/смету заново.").send()
        return

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
                await _attach_and_register_report(report)
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
        if len(session_docs) > 1:
            labels = "\n".join(f"- {name}" for name in session_docs.keys())
            await cl.Message(
                content=(
                    "Для анализа нужен один целевой документ. "
                    "Уточните, какой файл анализировать.\n"
                    f"{_active_set_status_line()}\n{labels}"
                )
            ).send()
            return
        only_name = next(iter(session_docs))
        file = {"name": only_name, "path": session_docs[only_name]["path"]}

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
                await _attach_and_register_report(report)
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
    all_docs = _get_all_docs()
    active_docs = _get_active_docs()
    target_doc_name = _resolve_target_doc_name(query, all_docs)

    scope_docs = active_docs
    if target_doc_name:
        scope_docs = [d for d in all_docs if str(d.get("display_name")) == target_doc_name]
    elif _is_report_query(query):
        report_docs = _get_report_docs()
        if report_docs:
            scope_docs = [report_docs[-1]]

    await _ensure_rag_index_for_doc_ids([str(d["document_id"]) for d in scope_docs])
    rag = cl.user_session.get("rag_pipeline")
    rag_result = None
    rag_meta: Dict[str, Any] = {}
    rag_mode = "simple"

    if rag and rag._indexed:
        async with cl.Step(name="Поиск по документам", type="retrieval") as step:
            try:
                retrieve_top_k = max(20, int(getattr(rag, "top_k", 5)) * 4) if target_doc_name else None
                rag_result = await asyncio.to_thread(rag.retrieve, query, retrieve_top_k)
                rag_meta = rag_result.metadata or {}
                rag_mode = str(rag_meta.get("mode", "simple"))
                intent = rag_result.intent or {}
                found_chunks = len(rag_result.chunks or [])
                target_hint = f", target_doc: {target_doc_name}" if target_doc_name else ""
                step.output = (
                    f"Найдено {found_chunks} релевантных фрагментов "
                    f"(mode: {rag_meta.get('mode', '?')}, "
                    f"intent: {intent.get('intent', '?')}, "
                    f"confidence: {intent.get('confidence', 0):.2f}{target_hint})"
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

    sources = _build_sources_from_rag_result(rag_result, rag, max_sources=20)
    if target_doc_name:
        sources = [s for s in sources if str(s.get("document_id")) == target_doc_name]
        sources = _reindex_sources(sources)
    if not sources:
        fallback_reason = None
        if target_doc_name:
            fallback_reason = (
                f"Для документа `{target_doc_name}` не найдено подтверждённых релевантных фрагментов "
                "в активном наборе."
            )
        fallback = _build_doc_question_deterministic_fallback(
            query=query,
            sources=[],
            fallback_type="insufficient_evidence",
            fallback_reason=fallback_reason,
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
            "answer_text": _strip_model_source_sections(response_text),
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
        "DocQuestion result: mode=%s chunks=%s citations=%s answer_mode=%s fallback=%s target_doc=%s top_raw=%.4f conf=%.2f",
        rag_mode,
        len(sources),
        len(cited_ids),
        payload["answer_mode"],
        payload["fallback_type"],
        target_doc_name or "-",
        float(sources[0]["raw_score"]) if sources else 0.0,
        payload["confidence"],
    )
    rendered = _render_doc_question_markdown(payload)
    msg = cl.Message(content=rendered)
    await msg.send()
    history.append({"role": "assistant", "content": rendered})


async def _handle_documents_summary(query: str, history: List):
    docs = _get_all_docs()
    if not docs:
        await cl.Message(content="Нет загруженных документов для суммаризации.").send()
        return

    per_doc: List[Dict[str, str]] = []
    async with cl.Step(name="Суммаризация документов", type="tool") as step:
        for doc in docs:
            doc_name = str(doc.get("display_name", "document"))
            text = str(doc.get("text", ""))[:10000]
            if not text.strip():
                per_doc.append({"name": doc_name, "summary": "Документ пуст или текст не извлечён."})
                continue
            prompt = _build_prompt(
                (
                    "Кратко суммаризируй документ в 4-6 пунктов: тема, цель, ключевые требования/положения, "
                    "сроки/ограничения (если есть), важные риски/последствия."
                ),
                [],
                (
                    "Ты аналитик документов. Пиши строго по тексту, без домыслов. "
                    f"Документ: {doc_name}\n\nТЕКСТ:\n{text}"
                ),
            )
            summary = await _infer_assistant_text(prompt, temperature=0.2)
            per_doc.append({"name": doc_name, "summary": summary.strip()})

        combined_input = "\n\n".join(
            f"[{idx+1}] {item['name']}\n{item['summary']}" for idx, item in enumerate(per_doc)
        )
        global_prompt = _build_prompt(
            "Собери общую сводку по набору документов.",
            [],
            (
                "Ты аналитик. На основе сводок по документам сформируй:\n"
                "1) ОБЩАЯ СВОДКА (5-8 предложений)\n"
                "2) КЛЮЧЕВЫЕ РАЗЛИЧИЯ/АКЦЕНТЫ (если документов больше одного) списком\n"
                "Пиши только на основе входных сводок.\n\n"
                f"СВОДКИ:\n{combined_input}"
            ),
        )
        global_summary = await _infer_assistant_text(global_prompt, temperature=0.2)
        step.output = f"Суммаризировано документов: {len(per_doc)}"

    lines = ["## Сводка по документам", "", "### По каждому документу"]
    for item in per_doc:
        lines.extend([f"#### {item['name']}", item["summary"], ""])
    lines.extend(["### Общая сводка", global_summary.strip()])
    rendered = "\n".join(lines).strip()
    await cl.Message(content=rendered).send()
    history.append({"role": "assistant", "content": rendered})


# === General Chat ===

GENERAL_CHAT_SYSTEM = (
    "Ты — Agent Navigator. Отвечай естественно, кратко и по делу, как универсальный чат-ассистент. "
    "Если пользователь общается в общем чате (приветствие, small talk, общие вопросы), "
    "не навязывай загрузку документов и не перечисляй специализацию без запроса.\n\n"
    "Когда пользователь явно просит анализ/сравнение/поиск по документам, переходи в профильную роль:\n"
    "- Сравнение двух документов (договоров, технических заданий, редакций) с выявлением изменений\n"
    "- Анализ соответствия сметы или КП техническому заданию (ТЗ vs смета/КП)\n"
    "- Ответы на вопросы по содержимому загруженных документов\n"
    "- Поиск конкретных условий, цифр и требований в документах\n\n"
    "Не выдумывай факты и не заявляй о доступе к интернету/новостям/курсам/погоде, если такого доступа нет."
)


async def _handle_chat(query: str, session_docs: Dict, history: List, social_query: bool = False):
    # Semantic Router определил needs_rag=False → отвечаем без контекста документов.
    # Даже если документы загружены — greeting/general_chat не нуждаются в RAG.
    prompt = _build_prompt(query, history, GENERAL_CHAT_SYSTEM)
    if social_query and not session_docs:
        prompt += (
            "\n\nКонтекст: документов в сессии нет. "
            "Ответь как обычный чат-ассистент, без предложений загрузить документы, "
            "если пользователь сам не просит анализ документов."
        )
    if social_query and session_docs:
        prompt += (
            "\n\nКонтекст: у пользователя уже есть загруженные документы. "
            "Отвечай как в обычном чате, без шаблонных фраз и без просьбы перезагрузить файлы."
        )
    msg = cl.Message(content="")
    await _stream_response(prompt, msg, history)
